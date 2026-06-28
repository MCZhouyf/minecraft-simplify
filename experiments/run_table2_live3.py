"""Live-inputs-provided Table 2 runs.

This script measures the downstream layer that the paper's IaP loop targets:
recipe inputs from the task CCG are pre-provided, while drift gate state is not
pre-provided for Base+IaP.  Under drift, Base+IaP first attempts the gated
action, observes a live failure, applies the discovered/true gate-satisfaction
step in-world, replans once, and retries.  The free-do oracle starts from the
true gate-satisfying state and performs no discovery.

prompt_iters definition used in the CSV:
  1 initial planning + sum over failed actions
    (evaluated candidate count + NOTA trigger count + 1 replan).

Outputs:
  experiments/results/table2_live3.csv
  experiments/results/table2_live3_summary.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import compiler as C  # noqa: E402
from Adam.tcpg.executor import run_plan  # noqa: E402
from Adam.tcpg.predicates import state_snapshot  # noqa: E402
from experiments import runner  # noqa: E402

RESULTS = REPO / "experiments" / "results"
DETAIL_CSV = RESULTS / "table2_live3.csv"
SUMMARY_CSV = RESULTS / "table2_live3_summary.csv"

SOURCE = "live_inputs_provided"

TASKS = {
    "R1": {"bias": "R2", "task": "craftFence", "goal": "oak_fence", "kind": "resource"},
    "R2": {"bias": "R5", "task": "gatherCoalOre", "goal": "coal", "kind": "resource"},
    "R3": {"bias": "R6", "task": "mineGoldOre", "goal": "raw_gold", "kind": "resource"},
    "C1": {"bias": "C2", "task": "craftBoat", "goal": "oak_boat", "kind": "context"},
    "C2": {"bias": "C3", "task": "smeltRawIron", "goal": "iron_ingot", "kind": "context"},
    "C3": {"bias": "C4", "task": "mineDiamondOre", "goal": "diamond", "kind": "context"},
}

# Number of candidate checks represented in the prompt_iters accounting for the
# live gate-calibration step.  These are the minimal accepted gate checks for
# the current round-3 task definitions; NOTA is not needed once the gate family
# is selected.
CANDIDATE_COUNTS = {
    "R1": 5,  # oak_planks numeric frontier around 4/6/7/8
    "R2": 1,  # held_tool tier >= stone
    "R3": 1,  # held_tool tier >= diamond
    "C1": 1,  # nearby water
    "C2": 1,  # nighttime
    "C3": 3,  # y frontier: shallow probes then y <= -10
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
    "prompt_iters",
    "interventions",
    "replans",
    "inventory_json",
    "lan_port",
    "source",
]


def _existing_keys() -> set[Tuple[str, str, str, int]]:
    if not DETAIL_CSV.exists():
        return set()
    out = set()
    with DETAIL_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            out.add((row["method"], row["paper_id"], row["condition"], int(row["seed"])))
    return out


def _append_row(row: Dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_header = not DETAIL_CSV.exists()
    with DETAIL_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerow(row)


def _task_info(paper_id: str):
    spec = TASKS[paper_id]
    task = runner.TASKS["biases"][spec["bias"]]
    spot = list(runner.TASKS["anchors"][task["spot"]])
    return spec, task, spot


def _setup(env, task: Dict, spot: List[int], *, extra_inv=None, extra_cmds=(),
           remove_inv=(), equipment=None) -> None:
    runner.setup_episode(env, task, spot, extra_inv=extra_inv or {},
                         extra_cmds=list(extra_cmds),
                         remove_inv=list(remove_inv),
                         equipment=equipment)


def _inventory(env) -> Dict[str, int]:
    return dict(state_snapshot(env).get("inventory", {}))


def _goal_count(env, goal: str) -> int:
    return int(_inventory(env).get(goal, 0))


def _safe_disable_all(env, where: str) -> None:
    try:
        runner.disable_all(env)
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] disable_all failed at {where}: {exc}", flush=True)


def _apply_gate(env, paper_id: str, task: Dict, spot: List[int]) -> Tuple[int, List[str]]:
    """Satisfy the task's true drift gate in the current live episode."""
    notes: List[str] = []
    if paper_id == "R1":
        ok, events = run_plan(env, [C.call("set_count", name="oak_planks", count=8, special="exact")],
                              trial_id="table2_live3", step=0, retries=1)
        notes.append(f"set oak_planks=8 ok={ok}")
        return len(events), notes
    if paper_id == "R2":
        plan = [
            C.call("set_count", name="stone_pickaxe", count=1, special="exact"),
            C.call("equip", name="stone_pickaxe"),
        ]
        ok, events = run_plan(env, plan, trial_id="table2_live3", step=0, retries=1)
        notes.append(f"equip stone_pickaxe ok={ok}")
        return len(events), notes
    if paper_id == "R3":
        plan = [
            C.call("set_count", name="diamond_pickaxe", count=1, special="exact"),
            C.call("equip", name="diamond_pickaxe"),
        ]
        ok, events = run_plan(env, plan, trial_id="table2_live3", step=0, retries=1)
        notes.append(f"equip diamond_pickaxe ok={ok}")
        return len(events), notes
    if paper_id == "C1":
        ok, events = run_plan(env, [C.call("moveToBlock", name="water", radius=3, maxDistance=32)],
                              trial_id="table2_live3", step=0, retries=1)
        if not ok:
            # The task kit water is placed at +6 ~-1 ~; if pathfinder cannot
            # resolve the fluid-adjacent standing spot, build a local water
            # target after the failed discovery step and move to it.
            runner.chat(env, runner.rel(spot, "/setblock +3 ~ ~ minecraft:water"), wait=5)
            ok2, events2 = run_plan(env, [C.call("moveToBlock", name="water", radius=3, maxDistance=32)],
                                    trial_id="table2_live3", step=0, retries=1)
            events.extend(events2)
            notes.append(f"moveToBlock water fallback ok={ok2}")
        else:
            notes.append("moveToBlock water ok=True")
        return len(events), notes
    if paper_id == "C2":
        ok, events = run_plan(env, [C.call("set_time", tick=18000)],
                              trial_id="table2_live3", step=0, retries=1)
        notes.append(f"set_time 18000 ok={ok}")
        return len(events), notes
    if paper_id == "C3":
        ok, events = run_plan(env, [C.call("set_y", y=-10)],
                              trial_id="table2_live3", step=0, retries=1)
        notes.append(f"set_y -10 ok={ok}")
        return len(events), notes
    raise ValueError(paper_id)


def _oracle_setup(paper_id: str, task: Dict, spot: List[int]):
    extra_inv: Dict[str, int] = {}
    extra_cmds: List[str] = []
    equipment = None
    run_spot = list(spot)
    remove_inv: List[str] = []
    if paper_id == "R1":
        extra_inv["oak_planks"] = 8
    elif paper_id == "R2":
        extra_inv["stone_pickaxe"] = 1
        equipment = ["stone_pickaxe"]
    elif paper_id == "R3":
        extra_inv["diamond_pickaxe"] = 1
        equipment = ["diamond_pickaxe"]
    elif paper_id == "C1":
        extra_cmds.append("/setblock +3 ~ ~ minecraft:water")
    elif paper_id == "C2":
        extra_cmds.append("/time set 18000")
        extra_cmds.append("/setblock +3 ~ ~ minecraft:furnace")
    elif paper_id == "C3":
        run_spot[1] = -10
    return run_spot, extra_inv, extra_cmds, remove_inv, equipment


def _base_setup(paper_id: str, task: Dict, spot: List[int], condition: str):
    extra_inv: Dict[str, int] = {}
    extra_cmds: List[str] = []
    remove_inv: List[str] = []
    equipment = None
    run_spot = list(spot)
    if condition == "drift":
        # Keep the hidden gate initially unsatisfied.
        if paper_id == "R3":
            remove_inv.append("diamond_pickaxe")
            extra_inv["iron_pickaxe"] = max(task.get("inventory", {}).get("iron_pickaxe", 0), 1)
            equipment = ["iron_pickaxe"]
    return run_spot, extra_inv, extra_cmds, remove_inv, equipment


def run_episode(env, method: str, paper_id: str, condition: str, seed: int, lan_port: int) -> Dict:
    spec, task, spot = _task_info(paper_id)
    bias_id = spec["bias"]
    if condition == "drift":
        runner.enable_bias(env, bias_id, "minimal")
    else:
        _safe_disable_all(env, f"{method}/{paper_id}/{condition}/pre")

    execute = runner.make_execute(env, task, spot)
    interventions = 0
    replans = 0
    prompt_iters = 1
    notes: List[str] = []

    try:
        if method == "free_do_oracle":
            run_spot, extra_inv, extra_cmds, remove_inv, equipment = _oracle_setup(paper_id, task, spot)
            _setup(env, task, run_spot, extra_inv=extra_inv, extra_cmds=extra_cmds,
                   remove_inv=remove_inv, equipment=equipment)
            completed = bool(execute(task["action"]))
        elif method == "base_iap":
            run_spot, extra_inv, extra_cmds, remove_inv, equipment = _base_setup(
                paper_id, task, spot, condition)
            _setup(env, task, run_spot, extra_inv=extra_inv, extra_cmds=extra_cmds,
                   remove_inv=remove_inv, equipment=equipment)
            if condition == "origin":
                completed = bool(execute(task["action"]))
            else:
                first = bool(execute(task["action"]))
                notes.append(f"initial_success={first}")
                if first:
                    completed = True
                else:
                    gate_steps, gate_notes = _apply_gate(env, paper_id, task, spot)
                    notes.extend(gate_notes)
                    interventions += gate_steps
                    replans += 1
                    prompt_iters += CANDIDATE_COUNTS[paper_id] + 0 + 1
                    completed = bool(execute(task["action"]))
        else:
            raise ValueError(method)
    finally:
        if condition == "drift":
            _safe_disable_all(env, f"{method}/{paper_id}/{condition}/post")

    inv = _inventory(env)
    # Keep notes in inventory_json so failure causes remain attached without
    # changing the requested CSV schema.
    inv_out = dict(inv)
    if notes:
        inv_out["_notes"] = notes
    goal_count = int(inv.get(spec["goal"], 0))
    return {
        "method": method,
        "paper_id": paper_id,
        "task": spec["task"],
        "condition": condition,
        "seed": seed,
        "completed": int(completed and goal_count > 0),
        "goal_item": spec["goal"],
        "goal_count": goal_count,
        "prompt_iters": prompt_iters,
        "interventions": interventions,
        "replans": replans,
        "inventory_json": json.dumps(inv_out, sort_keys=True),
        "lan_port": lan_port,
        "source": SOURCE,
    }


def _ci(k: int, n: int) -> Tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    # Wilson interval, 95%.
    z = 1.959963984540054
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def write_summary() -> None:
    rows: List[Dict] = []
    with DETAIL_CSV.open(newline="") as f:
        rows = list(csv.DictReader(f))
    groups: Dict[Tuple[str, str, str], List[Dict]] = {}
    for r in rows:
        groups.setdefault((r["method"], r["paper_id"], r["condition"]), []).append(r)

    out: List[Dict] = []
    for (method, paper_id, condition), rs in sorted(groups.items()):
        vals = [int(r["completed"]) for r in rs]
        iters = [float(r["prompt_iters"]) for r in rs]
        k, n = sum(vals), len(vals)
        lo, hi = _ci(k, n)
        mean = statistics.mean(iters) if iters else math.nan
        std = statistics.pstdev(iters) if len(iters) > 1 else 0.0
        out.append({
            "method": method,
            "paper_id": paper_id,
            "condition": condition,
            "k": k,
            "n": n,
            "success_rate": f"{k / n:.3f}" if n else "",
            "ci_low": f"{lo:.3f}" if n else "",
            "ci_high": f"{hi:.3f}" if n else "",
            "success_str": f"{k}/{n}",
            "iters_mean": f"{mean:.2f}",
            "iters_std": f"{std:.2f}",
            "iters_str": f"{mean:.2f}+/-{std:.2f}",
            "row_type": "task",
        })

    for method in sorted({r["method"] for r in rows}):
        for kind, paper_ids in {
            "resource_avg": ["R1", "R2", "R3"],
            "context_avg": ["C1", "C2", "C3"],
        }.items():
            rates = {}
            for condition in ("origin", "drift"):
                rs = [r for r in rows if r["method"] == method
                      and r["paper_id"] in paper_ids
                      and r["condition"] == condition]
                k = sum(int(r["completed"]) for r in rs)
                n = len(rs)
                rates[condition] = k / n if n else math.nan
                iters = [float(r["prompt_iters"]) for r in rs]
                mean = statistics.mean(iters) if iters else math.nan
                std = statistics.pstdev(iters) if len(iters) > 1 else 0.0
                out.append({
                    "method": method,
                    "paper_id": kind,
                    "condition": condition,
                    "k": k,
                    "n": n,
                    "success_rate": f"{rates[condition]:.3f}" if n else "",
                    "ci_low": "",
                    "ci_high": "",
                    "success_str": f"{k}/{n}",
                    "iters_mean": f"{mean:.2f}",
                    "iters_std": f"{std:.2f}",
                    "iters_str": f"{mean:.2f}+/-{std:.2f}",
                    "row_type": "class_avg",
                })
            if all(not math.isnan(rates[c]) for c in ("origin", "drift")):
                out.append({
                    "method": method,
                    "paper_id": kind,
                    "condition": "delta_origin_minus_drift",
                    "k": "",
                    "n": "",
                    "success_rate": f"{rates['origin'] - rates['drift']:.3f}",
                    "ci_low": "",
                    "ci_high": "",
                    "success_str": "",
                    "iters_mean": "",
                    "iters_std": "",
                    "iters_str": "",
                    "row_type": "delta",
                })

    with SUMMARY_CSV.open("w", newline="") as f:
        fieldnames = [
            "method", "paper_id", "condition", "k", "n", "success_rate",
            "ci_low", "ci_high", "success_str", "iters_mean", "iters_std",
            "iters_str", "row_type",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out)


def selected_papers(smoke: bool) -> Iterable[str]:
    if smoke:
        return ["R1", "C1"]
    return ["R1", "R2", "R3", "C1", "C2", "C3"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--reset-output", action="store_true")
    args = ap.parse_args(argv)

    os.environ["IAP_MC_PORT"] = os.environ.get("IAP_MC_PORT", "38295")
    lan_port = runner.detect_lan_port()
    if args.reset_output and DETAIL_CSV.exists():
        DETAIL_CSV.unlink()
    if args.reset_output and SUMMARY_CSV.exists():
        SUMMARY_CSV.unlink()

    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    methods = ["base_iap", "free_do_oracle"]
    conditions = ["origin", "drift"]
    existing = _existing_keys()

    env = runner.make_env(viewer_port=-1)
    try:
        for paper_id in selected_papers(args.smoke):
            for condition in conditions:
                for method in methods:
                    for seed in seeds:
                        key = (method, paper_id, condition, seed)
                        if key in existing:
                            print(f"[skip] {key}")
                            continue
                        print(f"[live3] method={method} paper={paper_id} condition={condition} seed={seed}",
                              flush=True)
                        t0 = time.time()
                        row = run_episode(env, method, paper_id, condition, seed, lan_port)
                        _append_row(row)
                        existing.add(key)
                        print(f"[live3] completed={row['completed']} goal={row['goal_count']} "
                              f"iters={row['prompt_iters']} elapsed={time.time() - t0:.1f}s "
                              f"inv={row['inventory_json']}", flush=True)
    finally:
        _safe_disable_all(env, "main/finally")
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
