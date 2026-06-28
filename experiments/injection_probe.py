"""Injection-validity probe (round-2 stage 2): verify each (modified) bias
ACTUALLY triggers failure when its true cause is unsatisfied, and succeeds when
satisfied. This is the check that X2 was missing in round 1 (6/6 episodes
succeeded because the bot started holding a shovel -> gate never fired).

For each bias it runs a 2-point test on the live world:
  - unsatisfied state -> execute action -> expect FAILURE (no goal produced)
  - satisfied state   -> execute action -> expect SUCCESS

Run AFTER enabling each bias, BEFORE the full matrix. Any bias that fails the
"unsatisfied -> failure" leg is a broken injection and must not enter跑批.

Usage:
  IAP_MC_PORT=... python3 experiments/injection_probe.py --bias X2
  IAP_MC_PORT=... python3 experiments/injection_probe.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TASKS = yaml.safe_load((REPO / "experiments" / "tasks.yaml").read_text())

# unsatisfied / satisfied state recipe per bias (what to set so the gate
# fails / passes), expressed as (inventory, equip, extra_commands).
PROBES = {
    # held_match gate: bare-hand (dirt) fails, shovel passes
    "X2": {"unsat": ({"wooden_shovel": 1, "dirt": 1}, "dirt", []),
           "sat":   ({"wooden_shovel": 1, "dirt": 1}, "wooden_shovel", [])},
    # tool-tier gates (datapack): low tier fails, high tier passes
    "C1": {"unsat": ({"stone_pickaxe": 1, "iron_pickaxe": 1}, "stone_pickaxe", []),
           "sat":   ({"stone_pickaxe": 1, "iron_pickaxe": 1}, "iron_pickaxe", [])},
    "C2": {"unsat": ({"iron_pickaxe": 1, "diamond_pickaxe": 1}, "iron_pickaxe", []),
           "sat":   ({"iron_pickaxe": 1, "diamond_pickaxe": 1}, "diamond_pickaxe", [])},
    "C3": {"unsat": ({"wooden_pickaxe": 1, "stone_pickaxe": 1}, "wooden_pickaxe", []),
           "sat":   ({"wooden_pickaxe": 1, "stone_pickaxe": 1}, "stone_pickaxe", [])},
    # inventory_min gates: without item fails, with item passes
    "R1": {"unsat": ({"cobblestone": 8, "crafting_table": 1}, None, []),
           "sat":   ({"cobblestone": 8, "crafting_table": 1, "coal": 1}, None, [])},
    "R2": {"unsat": ({"iron_ingot": 3, "stick": 2, "crafting_table": 1}, None, []),
           "sat":   ({"iron_ingot": 3, "stick": 2, "crafting_table": 1, "coal": 1}, None, [])},
    "R3": {"unsat": ({"oak_planks": 4, "stick": 2, "crafting_table": 1}, None, []),
           "sat":   ({"oak_planks": 4, "stick": 2, "crafting_table": 1, "birch_planks": 4}, None, [])},
    # nearby_block gate: no furnace fails, furnace nearby passes
    "P1": {"unsat": ({"iron_ingot": 3, "stick": 2, "crafting_table": 1}, None, []),
           "sat":   ({"iron_ingot": 3, "stick": 2, "crafting_table": 1, "furnace": 1},
                     None, ["/setblock ~2 ~ ~ minecraft:furnace"])},
    # y_level gate (now -10): above fails, below passes
    "X1": {"unsat": ({"iron_pickaxe": 1}, "iron_pickaxe", ["/tp @p ~ -6 ~"]),
           "sat":   ({"iron_pickaxe": 1}, "iron_pickaxe", ["/tp @p ~ -12 ~"])},
    # daytime gate: night fails, day passes
    "E1": {"unsat": ({"raw_iron": 2, "coal": 3, "furnace": 1}, None,
                     ["/time set 18000", "/setblock ~2 ~ ~ minecraft:furnace"]),
           "sat":   ({"raw_iron": 2, "coal": 3, "furnace": 1}, None,
                     ["/time set 1000", "/setblock ~2 ~ ~ minecraft:furnace"])},
    # sky_visible gate: roofed fails, open passes
    "E2": {"unsat": ({"oak_log": 2}, None, ["/setblock ~ ~2 ~ minecraft:dirt"]),
           "sat":   ({"oak_log": 2}, None, ["/setblock ~ ~2 ~ minecraft:air"])},
}


def make_env():
    from env.bridge import VoyagerEnv
    env = VoyagerEnv(mc_port=int(os.environ["IAP_MC_PORT"]),
                     server_port=int(os.environ.get("IAP_MF_PORT", "3000")),
                     request_timeout=180)
    env.reset(options={"mode": "hard", "inventory": {}})
    return env


def chat(env, *cmds, wait=15):
    code = "".join(f'bot.chat({json.dumps(c)});\nawait bot.waitForTicks({wait});\n'
                   for c in cmds)
    env.step(code)


def run_leg(env, runner, task, inv, equip, cmds):
    from Adam.tcpg.predicates import state_snapshot
    spot = TASKS["anchors"][task["spot"]]
    env.reset(options={"mode": "hard", "inventory": inv,
                       "position": {"x": spot[0], "y": spot[1], "z": spot[2]}})
    base = ["/gamerule doDaylightCycle false", "/gamerule doMobSpawning false",
            "/weather clear"]
    chat(env, *base, *cmds)
    if equip:
        from Adam.tcpg import compiler as C
        from Adam.tcpg.executor import run_plan
        run_plan(env, [C.call("equip", name=equip)])
    if task.get("ore"):
        blk, pos = task["ore"]
        chat(env, runner.rel(spot, f"/setblock {pos} minecraft:{blk}"))
    before = state_snapshot(env)["inventory"].get(task["goal"], 0)
    from Adam.skill_loader import skill_loader
    try:
        env.step(skill_loader(task["action"]))
    except Exception as exc:  # noqa: BLE001
        return False, f"exec error: {exc}"
    after = state_snapshot(env)["inventory"].get(task["goal"], 0)
    return after > before, f"goal {task['goal']}: {before}->{after}"


def probe_confound(env, runner, case_name):
    """A confound case must STILL produce a failure event under its modified
    setup (the discovery-flow trigger). This catches mirror cases like F3m
    that accidentally make the action always succeed (true cause + recipe
    inputs all satisfied -> no failure -> proposer never runs)."""
    spec = TASKS["suites"]["confound"]["cases"][case_name]
    bias_id = spec["bias"]
    task = TASKS["biases"][bias_id]
    spot = TASKS["anchors"][spec.get("spot_override") or task["spot"]]
    runner.enable_bias(env, bias_id)
    try:
        # replicate run_one's per-episode setup, then execute once
        runner.setup_episode(env, task, spot,
                             spec.get("inventory_extra"),
                             spec.get("commands_extra", ()),
                             remove_inv=spec.get("inventory_remove", ()))
        from Adam.tcpg.predicates import state_snapshot
        from Adam.skill_loader import skill_loader
        before = state_snapshot(env)["inventory"].get(task["goal"], 0)
        try:
            env.step(skill_loader(task["action"]))
        except Exception as exc:  # noqa: BLE001
            return {"case": case_name, "bias": bias_id, "triggers_failure": True,
                    "valid": True,
                    "detail": f"exec error (counts as failure): {exc}"}
        after = state_snapshot(env)["inventory"].get(task["goal"], 0)
    finally:
        runner.disable_all(env)
    failed = after <= before
    return {"case": case_name, "bias": bias_id, "triggers_failure": failed,
            "valid": failed,                    # confound is usable only if it fails
            "detail": f"goal {task['goal']}: {before}->{after} "
                      f"({'FAILS (good)' if failed else 'SUCCEEDS (BAD: no trigger)'})"}


def probe_bias(env, runner, bias_id):
    task = TASKS["biases"][bias_id]
    p = PROBES.get(bias_id)
    if not p:
        return {"bias": bias_id, "skip": "no probe defined"}
    runner.enable_bias(env, bias_id)
    try:
        ok_unsat, d1 = run_leg(env, runner, task, *p["unsat"])
        ok_sat, d2 = run_leg(env, runner, task, *p["sat"])
    finally:
        runner.disable_all(env)
    # VALID injection: unsatisfied -> FAILURE (not ok), satisfied -> SUCCESS (ok)
    valid = (not ok_unsat) and ok_sat
    return {"bias": bias_id, "valid": valid,
            "unsat_failed_as_expected": not ok_unsat, "unsat_detail": d1,
            "sat_succeeded_as_expected": ok_sat, "sat_detail": d2}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bias")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--confound", action="store_true",
                    help="probe confound cases (F1/F2/F3/F3m) for failure-trigger")
    args = ap.parse_args(argv)
    import experiments.runner as runner
    env = runner.make_env()
    results = []
    try:
        if args.confound:
            for case in TASKS["suites"]["confound"]["cases"]:
                r = probe_confound(env, runner, case)
                results.append(r)
                flag = "VALID " if r.get("valid") else "INVALID"
                print(f"{flag} {case} (bias {r['bias']}): {r['detail']}")
        else:
            ids = TASKS["suites"]["discovery"]["biases"] if args.all else [args.bias]
            for bid in ids:
                r = probe_bias(env, runner, bid)
                results.append(r)
                flag = "VALID " if r.get("valid") else "INVALID"
                print(f"{flag} {bid}: unsat_fail={r.get('unsat_failed_as_expected')} "
                      f"sat_succeed={r.get('sat_succeeded_as_expected')} | "
                      f"{r.get('unsat_detail','')} / {r.get('sat_detail','')}")
    finally:
        try:
            runner.disable_all(env)
            env.close()
        except Exception:
            pass
    suffix = "_confound" if args.confound else ""
    out = REPO / "experiments" / "results" / f"injection_probe{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    bad = [r.get("case") or r.get("bias") for r in results
           if not r.get("valid") and "skip" not in r]
    print(f"\n{len(results)} probed, {len(bad)} INVALID: {bad}")
    print(f"report -> {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
