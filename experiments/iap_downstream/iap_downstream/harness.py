"""The two-stage downstream-success harness.

Stage A (discovery + writeback) is assumed already done; it produced one frozen
causal graph ``G`` per method (``before``/``after``/``oracle``/ablations). Stage
B (this module) measures, on a **fresh empty-inventory world**, whether the
task can be completed from scratch using that frozen ``G`` - isolating the value
of what was discovered.

``downstream_success`` runs ``N`` seeded episodes for one (method, task,
condition, G) cell. ``run_sweep`` runs the full matrix and returns
:class:`~iap_downstream.metrics.DownstreamRow` rows ready for CSV + aggregation.
"""
from __future__ import annotations

from statistics import mean
from typing import Callable, Dict, List, Sequence, Tuple

from .causal_graph import CausalGraph
from .env_adapter import Env
from .executor import run_episode
from .metrics import DownstreamRow, wilson_ci

# An env factory creates a *fresh* environment for a given (task, condition,
# seed). Keeping it a factory guarantees a clean empty-inventory reset and full
# seed isolation between episodes.
EnvFactory = Callable[[str, str, int], Env]


def downstream_success(
    env_factory: EnvFactory,
    method: str,
    task: str,
    paper_id: str,
    condition: str,
    G: CausalGraph,
    n_seeds: int = 5,
    R_max: int = 3,
    step_budget: int = 200,
) -> DownstreamRow:
    """Run ``n_seeds`` episodes and summarise success for one matrix cell."""
    successes = 0
    steps: List[int] = []
    replans: List[int] = []
    for seed in range(n_seeds):
        env = env_factory(task, condition, seed)
        goals = env.goal_of(task)
        res = run_episode(env, goals, G, R_max=R_max, step_budget=step_budget)
        successes += int(res.success)
        steps.append(res.steps)
        replans.append(res.replans)
    lo, hi = wilson_ci(successes, n_seeds)
    return DownstreamRow(
        method=method,
        paper_id=paper_id,
        task=task,
        condition=condition,
        k=successes,
        n=n_seeds,
        success_rate=successes / n_seeds if n_seeds else 0.0,
        ci_low=lo,
        ci_high=hi,
        mean_steps=mean(steps) if steps else 0.0,
        mean_replans=mean(replans) if replans else 0.0,
    )


def run_sweep(
    env_factory: EnvFactory,
    graphs: Dict[str, CausalGraph],
    tasks: Sequence[Tuple[str, str]],  # (paper_id, task_name)
    conditions: Sequence[str] = ("origin", "drift"),
    n_seeds: int = 5,
    R_max: int = 3,
    step_budget: int = 200,
) -> List[DownstreamRow]:
    """Run the full method x task x condition matrix.

    ``graphs`` maps a method label (e.g. ``"after"``) to the frozen causal graph
    it should plan with.
    """
    rows: List[DownstreamRow] = []
    for method, G in graphs.items():
        for paper_id, task in tasks:
            for condition in conditions:
                rows.append(
                    downstream_success(
                        env_factory,
                        method=method,
                        task=task,
                        paper_id=paper_id,
                        condition=condition,
                        G=G,
                        n_seeds=n_seeds,
                        R_max=R_max,
                        step_budget=step_budget,
                    )
                )
    return rows


def sanity_checks(rows: Sequence[DownstreamRow]) -> List[str]:
    """Return a list of human-readable warnings if the attribution invariants
    are violated. Empty list == all good.

    Invariants (for the situational gated tasks):
      * origin:  after ~= before          (writeback did not break origin)
      * drift:   before ~= 0               (stale graph cannot solve drift)
      * drift:   after  > before           (discovery has end-to-end value)
    """
    warnings: List[str] = []
    by = {(r.method, r.paper_id, r.condition): r for r in rows}
    paper_ids = sorted({r.paper_id for r in rows})
    for pid in paper_ids:
        b_o = by.get(("before", pid, "origin"))
        a_o = by.get(("after", pid, "origin"))
        b_d = by.get(("before", pid, "drift"))
        a_d = by.get(("after", pid, "drift"))
        if a_o and b_o and abs(a_o.success_rate - b_o.success_rate) > 0.5:
            warnings.append(
                f"[{pid}] origin success differs a lot before vs after "
                f"({b_o.success_rate:.2f} -> {a_o.success_rate:.2f}); possible over-writeback"
            )
        if b_d and b_d.success_rate > 0.5:
            warnings.append(
                f"[{pid}] stale graph already solves drift ({b_d.success_rate:.2f}); "
                f"gate may not actually block the task"
            )
        if a_d and b_d and a_d.success_rate <= b_d.success_rate:
            warnings.append(
                f"[{pid}] drift success did not improve after writeback "
                f"({b_d.success_rate:.2f} -> {a_d.success_rate:.2f})"
            )
    return warnings
