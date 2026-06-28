"""Run the full Figure-2 IaP loop end-to-end (mock world by default).

    python -m iap_downstream.run_iap

The agent starts each situational task with an **empty inventory** and the
**stale** graph (no situational gate). It plans, an action fails, the
calibration loop (propose -> verify -> NOTA -> boundary -> write-back) discovers
the hidden gate online, the agent replans with the updated graph and completes
the task. We report, per task and drift condition: completion, the gate written
back, intervention budget, and replans.

This is the online sibling of ``run_downstream`` (frozen-graph before/after/
oracle). For the paper's success-rate tables, ``run_downstream`` is the clean
attribution experiment; this script demonstrates that the loop the tables stand
on actually closes.
"""
from __future__ import annotations

import argparse
import csv
import sys
from statistics import mean

from .agent import run_iap_episode
from .mock_env import MockMCDrift, build_mock_graphs, mock_tasks
from .proposer import MockProposer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Full IaP closed-loop (Figure 2) demo")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--cost-alpha", type=float, default=1.0)
    ap.add_argument("--no-dual-pool", action="store_true", help="ablate the dual pool (point estimate)")
    ap.add_argument("--conditions", nargs="+", default=["origin", "drift"])
    ap.add_argument("--real", action="store_true", help="use MC-Drift adapter + real/fallback LLM proposer")
    ap.add_argument("--out", default="", help="optional CSV output path")
    args = ap.parse_args(argv)

    if args.real:
        from experiments.iap_downstream_adapter.ccg_to_graph import load_real_graphs
        from experiments.iap_downstream_adapter.llm_proposer import LLMProposer
        from experiments.iap_downstream_adapter.mcdrift_env import MCDriftDownstreamEnv, real_tasks

        stale = load_real_graphs()["before"]
        proposer = LLMProposer()
        tasks = real_tasks()
        env_ctor = MCDriftDownstreamEnv
    else:
        stale = build_mock_graphs()["before"]  # agent starts ignorant of the gate
        proposer = MockProposer()
        tasks = mock_tasks()
        env_ctor = MockMCDrift
    posterior_cfg = {"mode": "point"} if args.no_dual_pool else None

    rows = []
    print(f"{'task':12s} {'cond':6s} {'done':>5s} {'succ%':>6s} {'itv':>5s} {'replan':>6s}  discovered")
    for paper_id, task in tasks:
        for cond in args.conditions:
            done = 0
            itvs, replans = [], []
            discovered_repr = ""
            for seed in range(args.seeds):
                env = env_ctor(task, cond, seed)
                r = run_iap_episode(
                    env, task, stale, proposer,
                    posterior_cfg=posterior_cfg, cost_alpha=args.cost_alpha, seed=seed,
                )
                done += int(r.completed)
                itvs.append(r.interventions)
                replans.append(r.replans)
                if r.discovered:
                    discovered_repr = "; ".join(f"{a}:{g}" for a, g in r.discovered)
                rows.append({
                    "paper_id": paper_id,
                    "task": task,
                    "condition": cond,
                    "seed": seed,
                    "completed": int(r.completed),
                    "steps": r.steps,
                    "interventions": r.interventions,
                    "replans": r.replans,
                    "discovered": "; ".join(f"{a}:{g}" for a, g in r.discovered),
                    "reason": r.reason,
                })
            rate = 100.0 * done / args.seeds
            print(
                f"{task:12s} {cond:6s} {done:>5d} {rate:6.1f} "
                f"{mean(itvs):5.1f} {mean(replans):6.1f}  {discovered_repr}"
            )
    if args.out:
        path = args.out
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {len(rows)} rows -> {path}")

    print(
        "\nExpected: drift -> 100% completion via online discovery of the gate "
        "(craftBoat: water_radius<=3; mineDiamond: y_level<=-8). "
        "origin -> 100% with no discovery needed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
