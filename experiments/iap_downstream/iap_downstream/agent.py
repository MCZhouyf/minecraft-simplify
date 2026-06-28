"""The integrated IaP agent - the whole Figure-2 loop.

    受领任务 -> 查询 CCG -> 规划 -> 执行高层动作
        -> 动作失败? -> 因果校准闭环 (propose/verify/intervene/NOTA/write-back)
        -> 用更新后的 CCG 重新规划 -> ... -> 完成任务

This is the online counterpart of the Stage-B harness: instead of being handed a
frozen graph, the agent *discovers* the gate when an action fails, writes it
back, replans with the updated graph and completes the task. The return value
records task completion **and** the discovery side-channel (interventions used,
gates written back), so one run yields both the downstream success number and
the discovery accounting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .calibration import calibrate
from .causal_graph import CausalGraph, GroundAction, Pred
from .env_adapter import Env
from .planner import plan as make_plan
from .proposer import Proposer


@dataclass
class AgentResult:
    completed: bool
    steps: int = 0                      # high-level actions executed
    interventions: int = 0              # Stage-A intervention budget (kept separate)
    replans: int = 0
    discovered: List[Tuple[str, str]] = field(default_factory=list)  # (action, gate_repr)
    reason: str = ""


def run_iap_episode(
    env: Env,
    task: str,
    G: CausalGraph,
    proposer: Proposer,
    *,
    posterior_cfg: Optional[dict] = None,
    cost_alpha: float = 1.0,
    R_max: int = 6,
    step_budget: int = 200,
    seed: int = 0,
) -> AgentResult:
    """Run one episode of the full Figure-2 loop on a fresh (empty-inventory) env."""
    goals = env.goal_of(task)
    res = AgentResult(completed=False)

    def goal_met() -> bool:
        return all(env.holds(g) for g in goals)

    if goal_met():
        return AgentResult(True, reason="goal already satisfied")

    plan: Optional[List[GroundAction]] = make_plan(goals, G, env.snapshot())
    if not plan:
        return AgentResult(goal_met(), reason="no initial plan")

    idx = 0
    while not goal_met() and res.steps < step_budget:
        if idx >= len(plan):
            if res.replans >= R_max:
                res.reason = "plan exhausted; replan budget spent"
                break
            plan = make_plan(goals, G, env.snapshot()) or []
            idx = 0
            res.replans += 1
            if not plan:
                res.reason = "replan empty"
                break
            continue

        ga = plan[idx]
        idx += 1
        result = env.step(ga)
        res.steps += 1

        if result.ok:
            continue

        # --- action failed -> trigger the causal-calibration loop -------- #
        if res.replans >= R_max:
            res.reason = f"action {ga.name} failed; replan budget spent"
            break
        cal = calibrate(
            ga.name,
            env,
            proposer,
            posterior_cfg=posterior_cfg,
            cost_alpha=cost_alpha,
            seed=seed + res.replans,
        )
        res.interventions += cal.interventions
        if cal.gate is not None:
            # only write back conditions with enough intervention evidence
            G = G.add_gate(ga.name, cal.gate)
            res.discovered.append((ga.name, repr(cal.gate)))
        # replan with the (possibly) updated graph and continue
        plan = make_plan(goals, G, env.snapshot()) or []
        idx = 0
        res.replans += 1
        if not plan:
            res.reason = "replan empty after calibration"
            break

    res.completed = goal_met()
    if res.completed and not res.reason:
        res.reason = "completed"
    elif not res.completed and not res.reason:
        res.reason = "step budget exhausted"
    return res
