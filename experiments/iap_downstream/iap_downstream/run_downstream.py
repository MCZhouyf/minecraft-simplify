"""CLI entry point for the downstream-success harness.

Out of the box this runs the **mock** MC-Drift world so you can see it working
end-to-end:

    python -m iap_downstream.run_downstream --out table_downstream.csv

To run against the real repo, implement an :class:`~iap_downstream.env_adapter.Env`
adapter and a ``load_graphs`` that reads your written-back CCG JSON into
:class:`~iap_downstream.causal_graph.CausalGraph`, then pass ``--real`` after
wiring ``REAL_ENV_FACTORY`` / ``REAL_GRAPHS`` below (see README).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from .causal_graph import CausalGraph
from .harness import run_sweep, sanity_checks
from .metrics import (
    to_table2,
    to_table3_success,
    to_table4_success,
    write_downstream_csv,
)
from .mock_env import build_mock_graphs, mock_env_factory, mock_tasks

ABLATIONS = ["minus_nota", "minus_boundary", "minus_dual_pool", "minus_costaware"]


# --------------------------------------------------------------------------- #
# Real-repo seams (fill these in when wiring to MC-Drift; see README)
# --------------------------------------------------------------------------- #
def real_env_factory(task: str, condition: str, seed: int):  # pragma: no cover
    from experiments.iap_downstream_adapter.mcdrift_env import mcdrift_env_factory

    return mcdrift_env_factory(task, condition, seed)


def real_graphs() -> Dict[str, CausalGraph]:  # pragma: no cover
    from experiments.iap_downstream_adapter.ccg_to_graph import load_real_graphs

    return load_real_graphs()


def real_tasks() -> List[Tuple[str, str]]:  # pragma: no cover
    from experiments.iap_downstream_adapter.mcdrift_env import real_tasks as _real_tasks

    return _real_tasks()


def _write_dict_rows(rows: List[Dict], path: Path) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IaP downstream-success harness (Stage B)")
    ap.add_argument("--out", default="table_downstream.csv", help="CSV output path")
    ap.add_argument("--seeds", type=int, default=5, help="episodes per cell")
    ap.add_argument("--rmax", type=int, default=3, help="max replans per episode")
    ap.add_argument("--step-budget", type=int, default=200)
    ap.add_argument("--real", action="store_true", help="use the real MC-Drift adapter")
    args = ap.parse_args(argv)

    if args.real:  # pragma: no cover
        env_factory = real_env_factory
        graphs = real_graphs()
        tasks = real_tasks()
    else:
        env_factory = mock_env_factory
        graphs = build_mock_graphs()
        tasks = mock_tasks()

    rows = run_sweep(
        env_factory,
        graphs,
        tasks,
        conditions=("origin", "drift"),
        n_seeds=args.seeds,
        R_max=args.rmax,
        step_budget=args.step_budget,
    )
    write_downstream_csv(rows, args.out)
    out_path = Path(args.out)
    _write_dict_rows(to_table2(rows), out_path.with_name("table2_downstream_success.csv"))
    _write_dict_rows(to_table3_success(rows, "after"),
                     out_path.with_name("table3_downstream_success.csv"))
    _write_dict_rows(to_table4_success(rows, ABLATIONS),
                     out_path.with_name("table4_downstream_success.csv"))

    # ---- console report ---- #
    print(f"\nwrote {len(rows)} rows -> {args.out}\n")
    print(f"{'method':14s} {'paper':5s} {'task':12s} {'cond':6s} {'succ':>5s}  {'ci':>14s}  steps")
    for r in rows:
        print(
            f"{r.method:14s} {r.paper_id:5s} {r.task:12s} {r.condition:6s} "
            f"{r.success_rate:5.2f}  [{r.ci_low:4.2f},{r.ci_high:4.2f}]  {r.mean_steps:4.1f}"
        )

    print("\n-- Table 4 success column (drift, by variant) --")
    for row in to_table4_success(rows, ABLATIONS):
        print(f"  {row['variant']:16s} {row['success_rate']:.2f}  "
              f"[{row['ci_low']:.2f},{row['ci_high']:.2f}]  n={row['n']}")

    print("\n-- Table 3 writeback-then-success column (after, drift) --")
    for row in to_table3_success(rows, "after"):
        print(f"  {row['paper_id']:5s} {row['writeback_success']:.2f}  n={row['n']}")

    warnings = sanity_checks(rows)
    if warnings:
        print("\n[!] sanity-check warnings:")
        for w in warnings:
            print("   -", w)
    else:
        print("\n[ok] attribution invariants hold "
              "(origin after==before, drift before==0, drift after>before).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
