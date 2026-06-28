"""Live no-CCG-prior LLM baselines for the six MC-Drift-Core tasks.

Baselines:
  adam_original: execute the original ADAM ActionLib skill for the target task;
    on failure, retry the same skill up to --max-replans.  No CCG, no drift
    discovery, no intervention verification, no writeback.

  reflection: execute the original ADAM ActionLib skill.  On failure, ask the
    LLM for one reflective repair skill from ADAM's checked-in ActionLib,
    execute that skill, then retry the target skill.  The reflection loop never
    validates candidates, never writes a CCG, and does not use IaP
    posterior/boundary/NOTA logic.  It also does not get privileged /give,
    /time, teleport, or direct gate-satisfaction commands.

The environment still uses each task's benchmark initial inventory/setup so the
baseline is tested on the original task (origin) and modified task (drift).
This is not the live-inputs-provided IaP table; source is live_no_ccg_prior.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.skill_loader import skill_loader  # noqa: E402
from Adam.tcpg.predicates import state_snapshot  # noqa: E402
from experiments import runner  # noqa: E402

RESULTS = REPO / "experiments" / "results"
DETAIL_CSV = RESULTS / "table2_llm_baselines_live3.csv"
SUMMARY_CSV = RESULTS / "table2_llm_baselines_task_summary.csv"
SOURCE = "live_no_ccg_prior"

TASKS = {
    "R1": {"bias": "R2", "task": "craftFence", "goal": "oak_fence", "kind": "resource"},
    "R2": {"bias": "R5", "task": "gatherCoalOre", "goal": "coal", "kind": "resource"},
    "R3": {"bias": "R6", "task": "mineGoldOre", "goal": "raw_gold", "kind": "resource"},
    "C1": {"bias": "C2", "task": "craftBoat", "goal": "oak_boat", "kind": "context"},
    "C2": {"bias": "C3", "task": "smeltRawIron", "goal": "iron_ingot", "kind": "context"},
    "C3": {"bias": "C4", "task": "mineDiamondOre", "goal": "diamond", "kind": "context"},
}

FIELDNAMES = [
    "method",
    "paper_id",
    "task",
    "condition",
    "seed",
    "completed",
    "goal_item",
    "goal_count",
    "replans",
    "reflection_calls",
    "inventory_json",
    "lan_port",
    "source",
]

ACTIONLIB_SKILLS = sorted(
    p.stem for p in (REPO / "Adam" / "ActionLib").glob("*.js")
)


def load_env_file(path: str | None) -> None:
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def existing_keys() -> set[Tuple[str, str, str, int]]:
    if not DETAIL_CSV.exists():
        return set()
    out = set()
    with DETAIL_CSV.open(newline="") as f:
        for r in csv.DictReader(f):
            out.add((r["method"], r["paper_id"], r["condition"], int(r["seed"])))
    return out


def append_row(row: Dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_header = not DETAIL_CSV.exists()
    with DETAIL_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerow(row)


def task_info(paper_id: str):
    spec = TASKS[paper_id]
    task = runner.TASKS["biases"][spec["bias"]]
    spot = list(runner.TASKS["anchors"][task["spot"]])
    return spec, task, spot


def inv(env) -> Dict[str, int]:
    return dict(state_snapshot(env).get("inventory", {}))


def safe_disable_all(env, where: str) -> None:
    try:
        runner.disable_all(env)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] disable_all failed at {where}: {exc}", flush=True)


def execute_adam_skill(env, task: Dict, spot: List[int]) -> bool:
    # Keep the same controlled ore/arena support used by the live IaP table, but
    # run only ADAM's original target skill.
    snap = state_snapshot(env)
    if task.get("ore"):
        base = (
            int(round(snap.get("agent.x", spot[0]))),
            int(round(snap.get("agent.y", spot[1]))),
            int(round(snap.get("agent.z", spot[2]))),
        )
        runner.prepare_ore_target(env, task, base)
    else:
        base = spot
        if task.get("setblocks_follow_agent"):
            base = (
                int(round(snap.get("agent.x", spot[0]))),
                int(round(snap.get("agent.y", spot[1]))),
                int(round(snap.get("agent.z", spot[2]))),
            )
        runner.prepare_setblocks(env, task, base)
    before = inv(env).get(task["goal"], 0)
    try:
        env.step(skill_loader(task["action"]))
    except Exception as exc:  # noqa: BLE001
        print(f"[baseline] {task['action']} step exception: {exc}", flush=True)
        return False
    after = inv(env).get(task["goal"], 0)
    return after > before


def reflection_prompt(paper_id: str, task: Dict, condition: str, inventory: Dict[str, int], last_error: str) -> str:
    allowed = [
        "gatherWoodLog", "craftPlanks", "craftSticks", "craftCraftingTable",
        "craftWoodenPickaxe", "craftStonePickaxe", "craftIronPickaxe",
        "gatherStone", "gatherCoalOre", "mineGoldOre", "mineDiamondOre",
        "smeltRawIron", "craftFence", "craftBoat",
        "moveForward", "moveBackward", "moveLeft", "moveRight", "moveUp", "moveDown",
    ]
    return f"""
You are controlling a Minecraft agent using reflection only. The previous
attempt to run ADAM's skill `{task['action']}` failed to produce `{task['goal']}`.

Benchmark condition: {condition}. Do not infer or update a causal graph. Do not
perform verification experiments. Choose one practical repair skill from ADAM's
existing skill library, then the agent will retry `{task['action']}` once.

Current inventory JSON: {json.dumps(inventory, sort_keys=True)}
Last failure note: {last_error}

Return only JSON with this schema:
{{"skill": "..."}}

Allowed repair skills:
{", ".join(allowed)}

You cannot use commands such as give, set_time, teleport, set_y, moveToBlock, or
directly grant the goal item.
""".strip()


def parse_reflection(text: str) -> Dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"skill": "retry"}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {"skill": "retry"}
    if not isinstance(obj, dict):
        return {"skill": "retry"}
    skill = str(obj.get("skill") or obj.get("action") or "retry")
    return {"skill": skill}


def llm_reflect(paper_id: str, task: Dict, condition: str, inventory: Dict[str, int], last_error: str) -> Dict:
    prompt = reflection_prompt(paper_id, task, condition, inventory, last_error)
    model = os.environ.get("IAP_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-5.1"
    try:
        from openai import OpenAI
        kwargs = {"timeout": 45.0}
        if os.environ.get("OPENAI_API_KEY"):
            kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
        if os.environ.get("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.environ["OPENAI_BASE_URL"].rstrip("/")
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        text = resp.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[reflection] LLM failed: {exc}", flush=True)
        return {"action": "retry", "args": {"error": str(exc)}}
    obj = parse_reflection(text)
    obj["_raw"] = text[:500]
    return obj


def apply_reflection(env, task: Dict, spot: List[int], action: Dict) -> str:
    skill = action.get("skill", "retry")
    if skill not in ACTIONLIB_SKILLS:
        return f"reflection_skill={skill} invalid_no_op"
    if skill == task["action"]:
        return f"reflection_skill={skill} same_as_target_no_op"
    # Reflection may use the original ADAM library only.  Keep the same
    # controlled ore support for mining skills, but do not issue privileged
    # command-style repairs.
    repair_task = dict(task)
    repair_task["action"] = skill
    goal_map = {
        "gatherWoodLog": "oak_log",
        "craftPlanks": "oak_planks",
        "craftSticks": "stick",
        "craftCraftingTable": "crafting_table",
        "craftWoodenPickaxe": "wooden_pickaxe",
        "craftStonePickaxe": "stone_pickaxe",
        "craftIronPickaxe": "iron_pickaxe",
        "gatherStone": "cobblestone",
        "gatherCoalOre": "coal",
        "mineGoldOre": "raw_gold",
        "mineDiamondOre": "diamond",
        "smeltRawIron": "iron_ingot",
        "craftFence": "oak_fence",
        "craftBoat": "oak_boat",
    }
    repair_task["goal"] = goal_map.get(skill, task["goal"])
    if skill == "gatherCoalOre":
        repair_task["ore"] = ["coal_ore", "+2 ~ ~"]
    elif skill == "mineGoldOre":
        repair_task["ore"] = ["gold_ore", "+2 ~ ~"]
    elif skill == "mineDiamondOre":
        repair_task["setblocks"] = [["+1 ~ ~", "diamond_ore"]]
        repair_task["setblocks_follow_agent"] = True
        repair_task.pop("ore", None)
    else:
        repair_task.pop("ore", None)
    ok = execute_adam_skill(env, repair_task, spot)
    return f"reflection_skill={skill} ok={ok}"


def run_episode(env, method: str, paper_id: str, condition: str, seed: int, lan_port: int,
                max_replans: int) -> Dict:
    spec, task, spot = task_info(paper_id)
    if condition == "drift":
        runner.enable_bias(env, spec["bias"], "minimal")
    else:
        safe_disable_all(env, f"{method}/{paper_id}/{condition}/pre")
    runner.setup_episode(env, task, spot)
    replans = 0
    reflection_calls = 0
    notes: List[str] = []
    try:
        success = execute_adam_skill(env, task, spot)
        while not success and replans < max_replans:
            replans += 1
            if method == "reflection":
                reflection_calls += 1
                repair = llm_reflect(paper_id, task, condition, inv(env), "target action produced no goal item")
                note = apply_reflection(env, task, spot, repair)
                notes.append(note)
                if "_raw" in repair:
                    notes.append("llm=" + str(repair["_raw"])[:240])
            else:
                notes.append("adam_original_retry")
            success = execute_adam_skill(env, task, spot)
    finally:
        if condition == "drift":
            safe_disable_all(env, f"{method}/{paper_id}/{condition}/post")
    inventory = inv(env)
    if notes:
        inventory = dict(inventory)
        inventory["_notes"] = notes
    goal_count = int(inv(env).get(spec["goal"], 0))
    return {
        "method": method,
        "paper_id": paper_id,
        "task": spec["task"],
        "condition": condition,
        "seed": seed,
        "completed": int(success and goal_count > 0),
        "goal_item": spec["goal"],
        "goal_count": goal_count,
        "replans": replans,
        "reflection_calls": reflection_calls,
        "inventory_json": json.dumps(inventory, sort_keys=True),
        "lan_port": lan_port,
        "source": SOURCE,
    }


def write_summary() -> None:
    rows = list(csv.DictReader(DETAIL_CSV.open())) if DETAIL_CSV.exists() else []
    out_fields = [
        "method", "paper_id", "task",
        "origin_success", "drift_success",
        "origin_replans_mean", "origin_replans_std", "origin_replans_str",
        "drift_replans_mean", "drift_replans_std", "drift_replans_str",
        "origin_n", "drift_n",
    ]
    records = []
    for method in ["adam_original", "reflection"]:
        for paper_id in ["R1", "R2", "R3", "C1", "C2", "C3"]:
            rs0 = [r for r in rows if r["method"] == method and r["paper_id"] == paper_id]
            task = rs0[0]["task"] if rs0 else TASKS[paper_id]["task"]
            rec = {"method": method, "paper_id": paper_id, "task": task}
            for cond in ["origin", "drift"]:
                rs = [r for r in rs0 if r["condition"] == cond]
                n = len(rs)
                k = sum(int(r["completed"]) for r in rs)
                replans = [float(r["replans"]) for r in rs]
                mean = statistics.mean(replans) if replans else 0.0
                std = statistics.pstdev(replans) if len(replans) > 1 else 0.0
                rec[f"{cond}_success"] = f"{k / n:.3f}" if n else ""
                rec[f"{cond}_replans_mean"] = f"{mean:.2f}"
                rec[f"{cond}_replans_std"] = f"{std:.2f}"
                rec[f"{cond}_replans_str"] = f"{mean:.2f}+/-{std:.2f}"
                rec[f"{cond}_n"] = n
            records.append(rec)
    with SUMMARY_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(records)


def papers(smoke: bool) -> Iterable[str]:
    return ["R1", "C1"] if smoke else ["R1", "R2", "R3", "C1", "C2", "C3"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default=".experiment-env")
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--methods", default="adam_original,reflection")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--reset-output", action="store_true")
    ap.add_argument("--max-replans", type=int, default=2)
    args = ap.parse_args(argv)
    load_env_file(args.env_file)
    if "IAP_MC_PORT" not in os.environ or os.environ.get("IAP_MC_PORT") == "36203":
        os.environ["IAP_MC_PORT"] = "46505"
    lan_port = runner.detect_lan_port()
    if args.reset_output:
        for p in [DETAIL_CSV, SUMMARY_CSV]:
            if p.exists():
                p.unlink()
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    existing = existing_keys()
    env = runner.make_env(viewer_port=-1)
    env.request_timeout = int(os.environ.get("BASELINE_STEP_TIMEOUT", "90"))
    try:
        for paper_id in papers(args.smoke):
            for condition in ["origin", "drift"]:
                for method in methods:
                    for seed in seeds:
                        key = (method, paper_id, condition, seed)
                        if key in existing:
                            print(f"[skip] {key}", flush=True)
                            continue
                        print(f"[baseline] method={method} paper={paper_id} condition={condition} seed={seed}",
                              flush=True)
                        t0 = time.time()
                        row = run_episode(env, method, paper_id, condition, seed, lan_port, args.max_replans)
                        append_row(row)
                        existing.add(key)
                        print(f"[baseline] completed={row['completed']} replans={row['replans']} "
                              f"elapsed={time.time()-t0:.1f}s inv={row['inventory_json']}", flush=True)
    finally:
        safe_disable_all(env, "main/finally")
        try:
            env.close(stop_process=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] env.close failed: {exc}", flush=True)
    write_summary()
    print(f"wrote {DETAIL_CSV}")
    print(f"wrote {SUMMARY_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
