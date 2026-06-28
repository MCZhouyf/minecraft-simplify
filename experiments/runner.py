"""Experiment runner (stage 7, loop mode): drives the verified TcpgRuntime
closed loop per (bias x mode x seed) over the live LAN world, producing the
K7 logs that evaluate.py aggregates into the paper tables.

Usage (one combo):
  python3 experiments/runner.py --suite discovery --bias C2 --mode tcpg --seed 0
Batch (all combos of a suite, resumable — finished runs are skipped):
  python3 experiments/runner.py --suite discovery --all
Smoke without an LLM key:
  python3 ... --scripted

Outputs per run -> experiments/runs/<suite>/<run_id>/
  k7.jsonl        every proposal/compile/intervention/posterior/writeback event
  episodes.jsonl  one record per episode (success, steps_used, decided so far)
  ccg.json        final conditional causal graph
  summary.json    run config + outcome digest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc_drift import datapack_gen as dg                       # noqa: E402
from Adam.tcpg.proposer import Candidate, validate            # noqa: E402

TASKS = yaml.safe_load((REPO / "experiments" / "tasks.yaml").read_text())
RUNS_DIR = REPO / "experiments" / "runs"
BIAS_SOURCE_PATH = Path(os.environ.get(
    "IAP_BIASES_PATH",
    str(REPO / "mc_drift" / "biases" / "biases.yaml"),
)).expanduser()
if not BIAS_SOURCE_PATH.is_absolute():
    BIAS_SOURCE_PATH = REPO / BIAS_SOURCE_PATH

# Injection routing is derived from each bias's declared mechanism (single
# source of truth = biases.yaml), NOT a hand-maintained id set. A datapack bias
# (datapack_recipe / datapack_tag) is enabled by toggling the world datapack;
# a mod_event bias is enabled by writing the Fabric-mod config. The round-2
# hard-coded {"C1","C2","C3"} went stale after the suite rename and routed 5/8
# new biases (R2/R5/R6 datapack, C1/C3 mod) to the wrong channel, so nothing
# was injected -- sanity_check then flagged them INJECTION INVALID.
_BIASES = yaml.safe_load(BIAS_SOURCE_PATH.read_text())["biases"]
_DATAPACK_MECHANISMS = {"datapack_recipe", "datapack_tag"}
DATAPACK_BIASES = {b["id"] for b in _BIASES
                   if b.get("mechanism") in _DATAPACK_MECHANISMS}
KNOWN_BIASES = {b["id"] for b in _BIASES}
EXTRA_DATAPACK_BIASES = {
    s.strip() for s in os.environ.get("IAP_EXTRA_DATAPACK_IDS", "").split(",")
    if s.strip()
}
DATAPACK_TOGGLE_BIASES = DATAPACK_BIASES | EXTRA_DATAPACK_BIASES

_ORACLE_STEPS = {b["id"]: (b.get("solvability") or {}).get("oracle_plan_steps")
                 for b in _BIASES}
_GROUND_TRUTH = {b["id"]: b.get("ground_truth", {}) for b in _BIASES}


def _property_match(target, gt_p, p):
    if target == "held_tool" and {str(gt_p), str(p)} <= {"pickaxe", "tier"}:
        return True
    if target == "time_of_day" and {str(gt_p), str(p)} <= {"clock", "time"}:
        return True
    return str(gt_p) == str(p)


def _value_match(target, gt_v, v):
    if target == "y_level":
        try:
            return abs(float(gt_v) - float(v)) <= 8
        except (TypeError, ValueError):
            return False
    if target == "time_of_day":
        try:
            a, b = map(float, gt_v)
            c, d = map(float, v)
            inter = max(0.0, min(b, d) - max(a, c))
            return inter / max(b - a, 1.0) >= 0.8
        except Exception:
            return False
    return str(gt_v) == str(v)


def _gt_accepted(bias_id: str, rt) -> bool:
    g = _GROUND_TRUTH.get(bias_id) or {}
    for c in rt.cands.values():
        if c.status != "accepted":
            continue
        if (c.target == g.get("target")
                and _property_match(g.get("target"), g.get("property"), c.property)
                and c.comparator == g.get("comparator")
                and _value_match(g.get("target"), g.get("value"), c.value)):
            return True
    return False


def _bias_oracle_steps(bias_id: str) -> int:
    """Oracle plan steps (App. C estimate) used only to size the per-run
    wall-time budget. A missing/None value (un-backfilled YAML) maps to the
    neutral default so the live runner never crashes on `None <= 50`."""
    v = _ORACLE_STEPS.get(bias_id)
    return int(v) if isinstance(v, (int, float)) else 100
DEFAULT_VIEWER_PORT = 3007


# ----------------------------------------------------------------- world I/O
def detect_lan_port() -> int:
    env_port = os.environ.get("IAP_MC_PORT") or os.environ.get("ADAM_MC_PORT")
    if env_port:
        os.environ["IAP_MC_PORT"] = env_port
        return int(env_port)

    log_path = Path("/root/.minecraft/logs/latest.log")
    if not log_path.exists():
        raise RuntimeError("No Minecraft latest.log found; open the world to LAN or set IAP_MC_PORT.")

    patterns = (
        re.compile(r"Started serving on (\d+)"),
        re.compile(r"Local game hosted on port (\d+)"),
    )
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in reversed(lines):
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            port = int(match.group(1))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
            except OSError:
                continue
            os.environ["IAP_MC_PORT"] = str(port)
            return port
    raise RuntimeError("No active Minecraft LAN port detected; open the world to LAN or set IAP_MC_PORT.")


def detect_mineflayer_port() -> int:
    env_port = os.environ.get("IAP_MF_PORT")
    if env_port:
        return int(env_port)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    os.environ["IAP_MF_PORT"] = str(port)
    return port


def open_viewer(viewer_port: int) -> None:
    viewer_url = f"http://127.0.0.1:{viewer_port}/"
    print(f"Mineflayer viewer URL: {viewer_url}")
    launcher = Path("/root/start-chrome-gpu.sh")
    if not launcher.exists():
        print("GPU Chrome launcher not found; open the viewer URL manually.")
        return
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":1")
    try:
        subprocess.Popen(
            [str(launcher), viewer_url],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("Opened Mineflayer viewer in GPU Chrome.")
    except Exception as exc:
        print(f"Failed to open GPU Chrome viewer: {exc}")


def make_env(viewer_port: int = -1):
    from env.bridge import VoyagerEnv
    if viewer_port != -1:
        os.environ.setdefault("ADAM_AUTO_REFRESH_VIEWER", "0")
    env = VoyagerEnv(mc_port=detect_lan_port(),
                     server_port=detect_mineflayer_port(),
                     request_timeout=180,
                     visual_server_port=viewer_port)
    env.reset(options={"mode": "hard", "inventory": {}})
    return env


def chat(env, *cmds, wait=20):
    code = "".join(f'bot.chat({json.dumps(c)});\nawait bot.waitForTicks({wait});\n'
                   for c in cmds)
    env.step(code)


def rel(env_spot, cmd):
    """'+3 ~ ~' style offsets -> absolute coords against the task anchor."""
    out = []
    coord_idx = 0
    max_coords = 6 if cmd.split()[:1] == ["/fill"] else 3
    for tok in cmd.split():
        axis = env_spot[coord_idx % 3] if coord_idx < max_coords else None
        if coord_idx < max_coords and (tok.startswith("+") or tok.startswith("-")):
            out.append(str(axis + int(tok)))
            coord_idx += 1
        elif tok == "~":
            out.append(str(axis))
            coord_idx += 1
        elif coord_idx < max_coords and tok.startswith("~"):
            out.append(str(axis + int(tok[1:])))
            coord_idx += 1
        else:
            out.append(tok)
    return " ".join(out)


def clear_matching_ores(env, spot, block_names):
    """Remove nearby natural copies so the action targets the controlled block."""
    x0, y0, z0 = spot
    xs = ((x0 - 32, x0), (x0 + 1, x0 + 32))
    zs = ((z0 - 32, z0), (z0 + 1, z0 + 32))
    cmds = []
    y_min = max(-64, y0 - 64)
    y_max = min(319, y0 + 64)
    y = y_min
    while y <= y_max:
        y2 = min(y + 15, y_max)
        for xa, xb in xs:
            for za, zb in zs:
                for block_name in block_names:
                    cmds.append(
                        f"/fill {xa} {y} {za} {xb} {y2} {zb} "
                        f"minecraft:stone replace minecraft:{block_name}"
                    )
        y = y2 + 1
    chat(env, *cmds, wait=1)


def prepare_ore_target(env, task, base):
    if not task.get("ore"):
        return
    block_name, _pos = task["ore"]
    names = [block_name]
    if block_name == "gold_ore":
        names.append("deepslate_gold_ore")
    elif block_name == "deepslate_gold_ore":
        names.append("gold_ore")
    clear_matching_ores(env, base, names)
    chat(
        env,
        rel(base, "/fill -1 ~ ~-1 +4 ~2 +1 minecraft:air"),
        rel(base, "/fill -1 ~-1 ~-1 +4 ~-1 +1 minecraft:stone"),
        rel(base, f"/setblock +2 ~ ~ minecraft:{block_name}"),
    )


def prepare_setblocks(env, task, base):
    cmds = [rel(base, f"/setblock {pos} minecraft:{blk}")
            for pos, blk in task.get("setblocks", [])]
    if cmds:
        chat(env, *cmds, wait=1)


def enable_bias(env, bias_id, feedback="minimal"):
    if bias_id not in KNOWN_BIASES:
        raise KeyError(f"unknown bias id {bias_id!r} in {BIAS_SOURCE_PATH}")
    cfg = dg.load_config()
    mod_path = Path(cfg["minecraft_dir"]).expanduser() / "config" / "mcdrift.json"
    if bias_id in DATAPACK_BIASES:
        dg.export_mod_config([], mod_path, biases_path=BIAS_SOURCE_PATH)
        env.datapacks_enable_only([bias_id], all_ids=sorted(DATAPACK_TOGGLE_BIASES))
    else:
        env.datapacks_enable_only([], all_ids=sorted(DATAPACK_TOGGLE_BIASES))
        dg.export_mod_config([bias_id], mod_path, biases_path=BIAS_SOURCE_PATH,
                             feedback_level=feedback)
        chat(env, "/mcdrift reload")
        time.sleep(2.5)


def disable_all(env):
    cfg = dg.load_config()
    dg.export_mod_config([], Path(cfg["minecraft_dir"]).expanduser()
                         / "config" / "mcdrift.json",
                         biases_path=BIAS_SOURCE_PATH)
    env.datapacks_enable_only([], all_ids=sorted(DATAPACK_TOGGLE_BIASES))
    chat(env, "/mcdrift reload")


# ----------------------------------------------------------------- episode kit
def setup_episode(env, task, spot, extra_inv=None, extra_cmds=(),
                  remove_inv=(), equipment=None):
    inv = dict(task["inventory"])
    inv.update(extra_inv or {})
    for item in (remove_inv or ()):          # confound cases may OMIT a base item
        inv.pop(item, None)
    reset_options = {"mode": "hard", "inventory": inv,
                     "position": {"x": spot[0], "y": spot[1], "z": spot[2]}}
    if equipment:
        eq = [None, None, None, None, None, None]
        eq[4] = list(equipment)[0]              # mineflayer reset mainhand slot
        reset_options["equipment"] = eq
    env.reset(options=reset_options)
    # Rebuild a small stable arena around the anchor every episode. Live runs
    # can leave holes/placed helpers from pathfinder mining or interrupted
    # interventions; if the bot falls below the anchor, staged ores at +2/+3 are
    # no longer reachable and freedo solvability gates falsely fail.
    arena_cmds = [
        rel(spot, "/fill -1 ~-1 ~-1 +4 ~-1 +1 minecraft:stone"),
        rel(spot, "/fill -1 ~ ~-1 +4 ~2 +1 minecraft:air"),
        f"/tp @s {spot[0]} {spot[1]} {spot[2]}",
        "/kill @e[type=item,distance=..128]",
    ]
    chat(env, *arena_cmds, wait=5)
    cmds = ["/gamerule doDaylightCycle false", "/time set 6000",
            "/gamerule doMobSpawning false", "/weather clear"]
    cmds += [rel(spot, f"/setblock {pos} minecraft:{blk}")
             for pos, blk in task.get("setblocks", [])]
    cmds += [rel(spot, c) for c in task.get("commands", [])]
    cmds += [rel(spot, c) for c in extra_cmds]
    chat(env, *cmds)
    prepare_ore_target(env, task, spot)
    equip_item = (list(equipment)[0] if equipment else task.get("equip"))
    if equip_item:
        from Adam.tcpg import compiler as C
        from Adam.tcpg.executor import run_plan
        run_plan(env, [C.call("equip", name=equip_item)])


def _freedo_oracle_gt_setup(bias_id):
    """Minimal I+ realization for freedo_oracle solvability smoke runs.

    The runtime's freedo mode zero-costs candidate interventions; it does not
    read suite ground truth. The Stage-1 gate needs a direct solvability check:
    initialize the episode at the GT satisfying state and verify the action can
    succeed. Keep this runner-side and mode-local so TCPG discovery remains
    blind to the answer.
    """
    gt = _GROUND_TRUTH.get(bias_id) or {}
    if gt.get("target") != "inventory_count" or gt.get("comparator") != ">=":
        inv_extra = {}
    else:
        prop, value = gt.get("property"), gt.get("value")
        inv_extra = {prop: value} if prop and isinstance(value, int) else {}

    equipment = []
    commands = []
    if gt.get("target") == "held_tool" and gt.get("comparator") == ">=":
        tier = str(gt.get("value"))
        tool = {
            "wooden": "wooden_pickaxe",
            "stone": "stone_pickaxe",
            "iron": "iron_pickaxe",
            "diamond": "diamond_pickaxe",
            "netherite": "netherite_pickaxe",
        }.get(tier)
        if tool:
            inv_extra[tool] = max(inv_extra.get(tool, 0), 1)
            equipment = [tool]
    if gt.get("target") == "nearby_block":
        block = gt.get("property")
        radius = gt.get("value")
        if block and isinstance(radius, int):
            # Place the required block inside the declared radius after the
            # episode arena has been cleared. Keep it off +1, where skills
            # commonly place the crafting table.
            dx = min(max(radius, 1), 3)
            commands.append(f"/setblock +{dx} ~ ~ minecraft:{block}")
    if gt.get("target") == "time_of_day":
        value = gt.get("value")
        if isinstance(value, list) and len(value) == 2:
            lo, hi = int(value[0]), int(value[1])
            commands.append(f"/time set {(lo + hi) // 2}")
        elif isinstance(value, int):
            commands.append(f"/time set {value}")
        # The smelting skill can place a furnace itself, but live C3 gate
        # checks are about furnace progress, not placement. Pre-place the
        # workstation for the GT smoke/oracle path so the solvability gate
        # isolates the nighttime condition.
        if bias_id == "C3":
            commands.append("/setblock +3 ~ ~ minecraft:furnace")
    spot_override = None
    if gt.get("target") == "y_level" and gt.get("comparator") == "<=":
        value = gt.get("value")
        if isinstance(value, int):
            spot_override = {"y": value}
    return {"inventory_extra": inv_extra, "equipment": equipment,
            "commands": commands, "spot_override": spot_override}


def _freedo_oracle_gt_preconds(bias_id):
    """Ground-truth precondition list used only by freedo_oracle.

    freedo_oracle starts each episode in a GT-satisfying state, so its success
    branch should verify that declared GT rather than the vanilla CCG
    assumptions. This matters for datapack_tag drifts such as R5/R6: vanilla
    coal mining assumes held_tool>=wooden, while the injected gate requires
    stone.
    """
    gt = dict(_GROUND_TRUTH.get(bias_id) or {})
    if not gt:
        return []
    if gt.get("target") == "held_tool":
        gt["property"] = "tier"
    bias = next((b for b in _BIASES if b["id"] == bias_id), {})
    return [{
        "dimension": bias.get("dimension", "resource"),
        "target": gt.get("target"),
        "property": gt.get("property"),
        "comparator": gt.get("comparator"),
        "value": gt.get("value"),
    }]


def _freedo_oracle_gt_candidate(bias_id, action):
    preconds = _freedo_oracle_gt_preconds(bias_id)
    if not preconds:
        return None
    p = preconds[0]
    c = Candidate(action, p["dimension"], p["target"], str(p["property"]),
                  p["comparator"], p["value"], source="freedo_oracle")
    ok, _why = validate(c)
    return c if ok else None


def _apply_gt_spot_override(spot, gt_setup):
    override = gt_setup.get("spot_override")
    if not override:
        return spot
    out = list(spot)
    if "x" in override:
        out[0] = override["x"]
    if "y" in override:
        out[1] = override["y"]
    if "z" in override:
        out[2] = override["z"]
    return out


def _freedo_oracle_inventory_extra(bias_id):
    """Backward-compatible helper for existing offline tests."""
    return _freedo_oracle_gt_setup(bias_id)["inventory_extra"]


def make_execute(env, task, spot):
    from Adam.tcpg.predicates import state_snapshot

    def execute(action):
        from Adam.skill_loader import skill_loader   # lazy: js bridge
        snap = state_snapshot(env)
        if task.get("ore"):
            base = (
                int(round(snap.get("agent.x", spot[0]))),
                int(round(snap.get("agent.y", spot[1]))),
                int(round(snap.get("agent.z", spot[2]))),
            )
            prepare_ore_target(env, task, base)
        else:
            base = spot
            if task.get("setblocks_follow_agent"):
                base = (
                    int(round(snap.get("agent.x", spot[0]))),
                    int(round(snap.get("agent.y", spot[1]))),
                    int(round(snap.get("agent.z", spot[2]))),
                )
            prepare_setblocks(env, task, base)
        before = snap["inventory"].get(task["goal"], 0)
        try:
            env.step(skill_loader(action))
        except Exception:
            return False
        return state_snapshot(env)["inventory"].get(task["goal"], 0) > before
    return execute


def scripted_llm_for(bias_id):
    gt = next(b for b in dg.load_biases(strict_actions=False) if b["id"] == bias_id)
    g = gt["ground_truth"]
    items = [{"dimension": gt["dimension"], "target": g["target"],
              "property": g["property"], "comparator": g["comparator"],
              "value": g["value"]},
             {"dimension": "resource", "target": "inventory_count",
              "property": "chest", "comparator": ">=", "value": 1}]
    return lambda prompt: json.dumps(items)


# ----------------------------------------------------------------- one run
def run_one(env, suite, bias_id, mode, seed, feedback="minimal",
            spot_override=None, inventory_extra=None, commands_extra=(),
            inventory_remove=(), scripted=False, case_name=None,
            cfg_overrides=None):
    from Adam.tcpg.ccg import CCG
    from Adam.tcpg.predicates import state_snapshot
    from Adam.tcpg.runtime import TcpgRuntime

    if bias_id not in TASKS["biases"]:
        print(f"[skip] bias {bias_id!r} has no tasks.yaml entry "
              f"(stale suite case); skipping")
        return
    task = TASKS["biases"][bias_id]
    spot = TASKS["anchors"][spot_override or task["spot"]]
    run_id = f"{case_name or bias_id}_{mode}_{feedback}_s{seed}"
    out = RUNS_DIR / suite / run_id
    if (out / "summary.json").exists():
        print(f"[skip] {run_id} (done)")
        return
    out.mkdir(parents=True, exist_ok=True)
    os.environ["IAP_K7_LOG"] = str(out / "k7.jsonl")

    enable_bias(env, bias_id, feedback)
    execute = make_execute(env, task, spot)
    llm = scripted_llm_for(bias_id) if scripted else None
    runtime_cfg = {
        "step_budget": task.get("step_budget", TASKS["defaults"]["step_budget"]),
        "max_interventions_per_event": task.get(
            "max_interventions_per_event",
            TASKS["defaults"]["max_interventions_per_event"]),
        "seed": seed,
        "M": 50_000,
    }
    # Tunables: defaults then per-bias override then explicit run override.
    # min_verifications_per_cand was silently dropped in round-2 (tasks.yaml
    # default never reached the runtime, which fell back to 0 and could leave
    # weak candidates undecided); it is now plumbed through. sim_verify_cost /
    # sim_cost_mode drive the 4.4 cost stratification; --cost-alpha / --min-floor
    # feed the table-6b ablation via cfg_overrides.
    for key in ("cost_alpha", "cost_c0", "trigger_budget",
                "min_verifications_per_cand", "sim_verify_cost", "sim_cost_mode",
                "n_min", "delta", "tau_acc", "tau_rej"):
        if key in TASKS["defaults"]:
            runtime_cfg[key] = TASKS["defaults"][key]
        if key in task:
            runtime_cfg[key] = task[key]
    if mode == "freedo_oracle":
        runtime_cfg["oracle_preconds"] = _freedo_oracle_gt_preconds(bias_id)
    if cfg_overrides:
        runtime_cfg.update(cfg_overrides)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode=mode,
                     execute_action=execute, llm=llm,
                     config=runtime_cfg,
                     trial_id=run_id)
    episodes = task.get("episodes", TASKS["defaults"]["episodes"])
    ep_f = (out / "episodes.jsonl").open("w")
    t0 = time.time()
    # Per-run wall-time budget scaled by the bias's oracle plan steps (App. C):
    # cheap biases finish fast; deep/long ones get more headroom. Prevents a
    # single run from hanging the matrix (round-1 X1 froze on in-world moveTo).
    oracle = _bias_oracle_steps(bias_id)
    time_budget_s = 1200 if oracle <= 50 else (1800 if oracle <= 150 else 2700)
    aborted = False
    try:
        for ep in range(episodes):
            if time.time() - t0 > time_budget_s:
                aborted = True
                print(f"[{run_id}] TIME LIMIT {time_budget_s}s hit at ep{ep}, aborting")
                break
            inv_extra = dict(inventory_extra or {})
            equipment = None
            if mode == "freedo_oracle":
                gt_setup = _freedo_oracle_gt_setup(bias_id)
                inv_extra.update(gt_setup["inventory_extra"])
                equipment = gt_setup["equipment"] or None
                ep_extra_cmds = list(commands_extra) + gt_setup.get("commands", [])
                ep_spot = _apply_gt_spot_override(spot, gt_setup)
            else:
                ep_extra_cmds = commands_extra
                ep_spot = spot
            setup_episode(env, task, ep_spot, inv_extra, ep_extra_cmds,
                          remove_inv=inventory_remove, equipment=equipment)
            y = execute(task["action"])
            inv = state_snapshot(env)["inventory"]
            if mode == "freedo_oracle" and y:
                oracle_cand = _freedo_oracle_gt_candidate(bias_id, task["action"])
                if oracle_cand is not None:
                    oracle_cand.status = "accepted"
                    oracle_cand.decided_step = ep + 1
                    rt.cands[oracle_cand.cid] = oracle_cand
                    rt.ccg.write_back(oracle_cand, run_id, ep + 1)
                rt.steps_used += 1
            else:
                rt.on_action(task["action"], y, inv, {"inventory": dict(inv)})
            decided = {c.target: c.status for c in rt.cands.values()
                       if c.status in ("accepted", "rejected")}
            ep_f.write(json.dumps({"episode": ep, "natural_success": bool(y),
                                   "steps_used": rt.steps_used,
                                   "decided": decided}) + "\n")
            ep_f.flush()
            print(f"[{run_id}] ep{ep} y={int(y)} steps={rt.steps_used} "
                  f"decided={decided}")
            if _gt_accepted(bias_id, rt):
                print(f"[{run_id}] gt accepted at ep{ep}, ending run")
                break
    finally:
        ep_f.close()
        rt.ccg.save(out / "ccg.json")
        (out / "summary.json").write_text(json.dumps({
            "run_id": run_id, "suite": suite, "bias": bias_id, "mode": mode,
            "feedback": feedback, "seed": seed, "scripted": scripted,
            "episodes": episodes, "steps_used": rt.steps_used,
            "wall_seconds": round(time.time() - t0, 1),
            "time_budget_s": time_budget_s, "aborted": aborted,
            "cost_alpha": runtime_cfg.get("cost_alpha"),
            "min_verifications_per_cand":
                runtime_cfg.get("min_verifications_per_cand"),
            "sim_cost_mode": runtime_cfg.get("sim_cost_mode"),
            "nota_reproposal": runtime_cfg.get("nota_reproposal"),
            "max_reproposal_rounds": runtime_cfg.get("max_reproposal_rounds"),
            "config_overrides": cfg_overrides or {},
            "candidates": rt.candidate_records(),
        }, indent=2))
        disable_all(env)


def run_solvability_smoke(env, suite, bias_id, seed, feedback="minimal",
                          spot_override=None, inventory_extra=None,
                          commands_extra=(), inventory_remove=(),
                          case_name=None):
    from Adam.tcpg.predicates import state_snapshot

    if bias_id not in TASKS["biases"]:
        print(f"[skip] bias {bias_id!r} has no tasks.yaml entry "
              f"(stale suite case); skipping")
        return
    task = TASKS["biases"][bias_id]
    spot = TASKS["anchors"][spot_override or task["spot"]]
    run_id = f"{case_name or bias_id}_solvability_smoke_s{seed}"
    out = RUNS_DIR / suite / run_id
    if (out / "summary.json").exists():
        print(f"[skip] {run_id} (done)")
        return
    out.mkdir(parents=True, exist_ok=True)
    os.environ["IAP_K7_LOG"] = str(out / "k7.jsonl")

    enable_bias(env, bias_id, feedback)
    execute = make_execute(env, task, spot)
    t0 = time.time()
    inv_extra = dict(inventory_extra or {})
    gt_setup = _freedo_oracle_gt_setup(bias_id)
    inv_extra.update(gt_setup["inventory_extra"])
    equipment = gt_setup["equipment"] or None
    y = False
    inv = {}
    try:
        smoke_spot = _apply_gt_spot_override(spot, gt_setup)
        setup_episode(env, task, smoke_spot, inv_extra,
                      list(commands_extra) + gt_setup.get("commands", []),
                      remove_inv=inventory_remove, equipment=equipment)
        y = execute(task["action"])
        inv = state_snapshot(env)["inventory"]
        (out / "episodes.jsonl").write_text(json.dumps({
            "episode": 0,
            "natural_success": bool(y),
            "smoke": True,
            "gt_setup": gt_setup,
        }) + "\n")
        (out / "k7.jsonl").write_text(json.dumps({
            "type": "solvability_smoke",
            "run_id": run_id,
            "bias": bias_id,
            "action": task["action"],
            "success": bool(y),
            "gt_setup": gt_setup,
            "inventory": inv,
        }) + "\n")
        (out / "ccg.json").write_text("{}\n")
        (out / "summary.json").write_text(json.dumps({
            "run_id": run_id,
            "suite": suite,
            "bias": bias_id,
            "mode": "solvability_smoke",
            "feedback": feedback,
            "seed": seed,
            "episodes": 1,
            "natural_success": bool(y),
            "wall_seconds": round(time.time() - t0, 1),
            "gt_setup": gt_setup,
            "inventory": inv,
        }, indent=2))
        print(f"[{run_id}] y={int(y)} gt_setup={gt_setup}")
    finally:
        disable_all(env)


def run_natural_smoke(env, suite, bias_id, seed, feedback="minimal",
                      spot_override=None, inventory_extra=None,
                      commands_extra=(), inventory_remove=(),
                      case_name=None):
    """Run one unmodified task episode to verify the injected I- leg fails."""
    from Adam.tcpg.predicates import state_snapshot

    if bias_id not in TASKS["biases"]:
        print(f"[skip] bias {bias_id!r} has no tasks.yaml entry "
              f"(stale suite case); skipping")
        return
    task = TASKS["biases"][bias_id]
    spot = TASKS["anchors"][spot_override or task["spot"]]
    run_id = f"{case_name or bias_id}_natural_smoke_s{seed}"
    out = RUNS_DIR / suite / run_id
    if (out / "summary.json").exists():
        print(f"[skip] {run_id} (done)")
        return
    out.mkdir(parents=True, exist_ok=True)
    os.environ["IAP_K7_LOG"] = str(out / "k7.jsonl")

    enable_bias(env, bias_id, feedback)
    execute = make_execute(env, task, spot)
    t0 = time.time()
    y = False
    inv = {}
    try:
        setup_episode(env, task, spot, dict(inventory_extra or {}),
                      commands_extra, remove_inv=inventory_remove)
        y = execute(task["action"])
        inv = state_snapshot(env)["inventory"]
        (out / "episodes.jsonl").write_text(json.dumps({
            "episode": 0,
            "natural_success": bool(y),
            "smoke": True,
        }) + "\n")
        (out / "k7.jsonl").write_text(json.dumps({
            "type": "natural_smoke",
            "run_id": run_id,
            "bias": bias_id,
            "action": task["action"],
            "success": bool(y),
            "inventory": inv,
        }) + "\n")
        (out / "ccg.json").write_text("{}\n")
        (out / "summary.json").write_text(json.dumps({
            "run_id": run_id,
            "suite": suite,
            "bias": bias_id,
            "mode": "natural_smoke",
            "feedback": feedback,
            "seed": seed,
            "episodes": 1,
            "natural_success": bool(y),
            "wall_seconds": round(time.time() - t0, 1),
            "inventory": inv,
        }, indent=2))
        print(f"[{run_id}] y={int(y)}")
    finally:
        disable_all(env)


# ----------------------------------------------------------------- batch
def combos(suite_name, scripted):
    s = TASKS["suites"][suite_name]
    seeds = TASKS["defaults"]["seeds"]
    if suite_name == "confound":
        for case, spec in s["cases"].items():
            for mode in s["modes"]:
                for seed in seeds:
                    yield dict(bias_id=spec["bias"], mode=mode, seed=seed,
                               spot_override=spec.get("spot_override"),
                               inventory_extra=spec.get("inventory_extra"),
                               commands_extra=spec.get("commands_extra", ()),
                               inventory_remove=spec.get("inventory_remove", ()),
                               case_name=case, scripted=scripted)
        return
    feedbacks = s["feedback"] if isinstance(s["feedback"], list) else [s["feedback"]]
    for b in s["biases"]:
        for mode in s["modes"]:
            for fb in feedbacks:
                for seed in seeds:
                    yield dict(bias_id=b, mode=mode, seed=seed, feedback=fb,
                               scripted=scripted)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", required=True, choices=list(TASKS["suites"]))
    ap.add_argument("--bias")
    ap.add_argument("--mode")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--solvability-smoke", action="store_true",
                    help="run one GT-satisfying execute(action) gate; no LLM/runtime loop")
    ap.add_argument("--natural-smoke", action="store_true",
                    help="run one unmodified injected task episode; expected to fail")
    ap.add_argument("--scripted", action="store_true",
                    help="scripted proposer (smoke only; real runs need an LLM key)")
    ap.add_argument("--no-viewer", action="store_true",
                    help="disable Prismarine Viewer for headless batch runs")
    ap.add_argument("--cost-alpha", type=float, default=None,
                    help="override cost-sensitive strength alpha (table-6b ablation)")
    ap.add_argument("--min-floor", type=int, default=None,
                    help="override min_verifications_per_cand "
                         "(0 = pure cost-aware ordering, for the table-6b ablation)")
    ap.add_argument("--sim-cost-mode", choices=["floor", "flat"], default=None,
                    help="cost stratification mode (floor = paper 4.4; flat = round-2)")
    ap.add_argument("--case-name", default=None,
                    help="run_id prefix override (keeps ablation runs side by side)")
    ap.add_argument("--run-id-prefix", default=None,
                    help="prefix generated --all case names, e.g. nota -> nota_F1")
    ap.add_argument("--seeds", default=None,
                    help="comma-separated seed list for --all (e.g. '0,1,2'); "
                         "overrides tasks.yaml defaults.seeds. Manual sec.2 step 3 "
                         "wants >=3 seeds for the three-mode discovery comparison.")
    ap.add_argument("--no-necessity", action="store_true",
                    help="ablation: drop the success/necessity branch (paper Table 6)")
    ap.add_argument("--no-neighbor", action="store_true",
                    help="ablation: drop ordered-domain neighbor expansion (Table 6)")
    ap.add_argument("--no-dual-pool", action="store_true",
                    help="ablation: use one-sided point-estimate posterior decisions")
    ap.add_argument("--nota-reproposal", action="store_true",
                    help="enable none-of-the-above reproposal tail")
    ap.add_argument("--max-reproposal-rounds", type=int, default=2,
                    help="maximum NOTA reproposal rounds when --nota-reproposal is enabled")
    ap.add_argument("--config-override", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="override an arbitrary TcpgRuntime config key; repeatable")
    ap.add_argument("--viewer-port", type=int,
                    default=int(os.environ.get("ADAM_VIEWER_PORT", DEFAULT_VIEWER_PORT)))
    args = ap.parse_args(argv)
    if not args.scripted and not args.solvability_smoke and not args.natural_smoke:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key or api_key == "...":
            raise SystemExit(
                "Real experiment runs need a valid OPENAI_API_KEY. "
                "Use --scripted for smoke tests, or export a real key "
                "and OPENAI_BASE_URL if you use a proxy."
            )
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError:
            raise SystemExit(
                "OPENAI_API_KEY contains non-ASCII characters. Export the raw key only, "
                "for example OPENAI_API_KEY='sk-...'; do not include Chinese labels."
            )
    # CLI ablation flags -> runtime cfg overrides (highest precedence, applied
    # after tasks.yaml defaults + per-bias values inside run_one). Used for the
    # table-6b cost-sensitivity ablation: e.g. --cost-alpha 0 --min-floor 0
    # reproduces the cost-blind failure, --cost-alpha 0.5 the default.
    overrides = {}
    if args.cost_alpha is not None:
        overrides["cost_alpha"] = args.cost_alpha
    if args.min_floor is not None:
        overrides["min_verifications_per_cand"] = args.min_floor
    if args.sim_cost_mode is not None:
        overrides["sim_cost_mode"] = args.sim_cost_mode
    if args.no_necessity:
        overrides["necessity_test"] = False
    if args.no_neighbor:
        overrides["neighbor_expand"] = False
    if args.no_dual_pool:
        overrides["posterior_mode"] = "point"
    if args.nota_reproposal:
        overrides["nota_reproposal"] = True
        overrides["max_reproposal_rounds"] = args.max_reproposal_rounds
    for item in args.config_override:
        if "=" not in item:
            raise SystemExit(f"--config-override expects KEY=VALUE, got {item!r}")
        key, raw = item.split("=", 1)
        raw_l = raw.strip().lower()
        if raw_l in ("true", "false"):
            val = raw_l == "true"
        else:
            try:
                val = int(raw)
            except ValueError:
                try:
                    val = float(raw)
                except ValueError:
                    val = raw
        overrides[key.strip()] = val
    overrides = overrides or None

    if args.seeds:                       # --seeds 0,1,2 -> batch seed list
        TASKS["defaults"]["seeds"] = [int(x) for x in args.seeds.split(",") if x.strip() != ""]

    viewer_port = -1 if args.no_viewer else args.viewer_port
    env = make_env(viewer_port=viewer_port)
    if viewer_port != -1:
        open_viewer(viewer_port)
    try:
        if args.solvability_smoke:
            run_solvability_smoke(env, args.suite, args.bias, args.seed or 0,
                                  case_name=args.case_name)
        elif args.natural_smoke:
            run_natural_smoke(env, args.suite, args.bias, args.seed or 0,
                              case_name=args.case_name)
        elif args.all:
            for kw in combos(args.suite, args.scripted):
                if args.run_id_prefix:
                    base = kw.get("case_name") or kw["bias_id"]
                    kw["case_name"] = f"{args.run_id_prefix}_{base}"
                # confound combos already carry a per-case case_name; only fill
                # in the CLI override where the suite did not set one (avoids a
                # duplicate-keyword TypeError on the confound suite).
                kw.setdefault("case_name", args.case_name)
                run_one(env, args.suite, cfg_overrides=overrides, **kw)
        else:
            run_one(env, args.suite, args.bias, args.mode, args.seed or 0,
                    scripted=args.scripted, cfg_overrides=overrides,
                    case_name=args.case_name)
    finally:
        try:
            disable_all(env)
            env.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
