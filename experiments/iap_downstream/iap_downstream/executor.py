"""Closed-loop execution of a plan with bounded replanning.

The executor consumes a plan action-by-action, monitoring outcomes against the
environment. When an action fails (its world preconditions did not hold) or the
plan is exhausted before the goal is met, it replans from the **current
observable state** using the **frozen** causal graph ``G`` (no learning happens
downstream). Replanning is bounded by ``R_max`` and total work by
``step_budget`` so termination is guaranteed.

Key property: if ``G`` is missing/wrong about a gate, replanning with the same
``G`` reproduces the same failing plan, so the episode correctly fails after
``R_max`` attempts. Replanning only helps against *transient*/stochastic
failures or partial observability, never against missing knowledge - which is
exactly what we want when measuring the value of what was discovered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .causal_graph import CausalGraph, GroundAction, Pred
from .env_adapter import Env
from .planner import plan as make_plan


@dataclass
class EpisodeResult:
    success: bool
    steps: int
    replans: int
    reason: str = ""


def run_episode(
    env: Env,
    goals: Sequence[Pred],
    G: CausalGraph,
    R_max: int = 3,
    step_budget: int = 200,
) -> EpisodeResult:
    """Plan from the env's current state, then execute closed-loop with replan."""
    steps = 0
    replans = 0

    def goal_met() -> bool:
        return all(env.holds(g) for g in goals)

    if goal_met():
        return EpisodeResult(True, 0, 0, "goal already satisfied")

    plan: Optional[List[GroundAction]] = make_plan(goals, G, env.snapshot())
    if not plan:
        return EpisodeResult(goal_met(), steps, replans, "no initial plan")

    idx = 0
    while not goal_met() and steps < step_budget:
        if idx >= len(plan):
            # plan ran out without reaching the goal -> replan or give up
            if replans >= R_max:
                return EpisodeResult(False, steps, replans, "plan exhausted; replan budget spent")
            plan = make_plan(goals, G, env.snapshot()) or []
            idx = 0
            replans += 1
            if not plan:
                return EpisodeResult(goal_met(), steps, replans, "replan produced empty plan")
            continue

        ga = plan[idx]
        idx += 1
        result = env.step(ga)
        steps += 1

        if not result.ok:
            if replans >= R_max:
                return EpisodeResult(False, steps, replans, f"action {ga.name} failed; replan budget spent")
            plan = make_plan(goals, G, env.snapshot()) or []
            idx = 0
            replans += 1
            if not plan:
                return EpisodeResult(goal_met(), steps, replans, "replan produced empty plan")

    return EpisodeResult(goal_met(), steps, replans, "ok" if goal_met() else "step budget exhausted")
