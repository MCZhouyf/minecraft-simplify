"""Planning over a (frozen) causal graph.

``regress_plan`` is a total-order STRIPS goal-regression planner: for every
unsatisfied goal predicate it picks an achiever from ``G``, recurses on that
achiever's preconditions, then emits the achiever. The result is validated by
forward simulation (:func:`validate_plan`); if regression fails or yields an
invalid plan, :func:`bfs_plan` (a bounded forward search) is used as a safety
net. The downstream harness calls :func:`plan` which ties these together.

The model assumes monotone tasks (no effect deletes a previously-needed fact),
which holds for the MC-Drift resource/situational tasks; the validator catches
any violation and triggers the fallback.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .causal_graph import (
    Atom,
    CausalGraph,
    GroundAction,
    Pred,
    State,
    Threshold,
    ground,
    pred_key,
)


# --------------------------------------------------------------------------- #
# Goal regression
# --------------------------------------------------------------------------- #
def regress_plan(
    goals: Sequence[Pred],
    G: CausalGraph,
    state: State,
    max_actions: int = 64,
) -> Optional[List[GroundAction]]:
    """Return a totally-ordered plan achieving ``goals`` from ``state`` under ``G``.

    Returns ``None`` if no plan is found within ``max_actions``.
    """
    plan: List[GroundAction] = []
    achieved: set = set()  # pred keys already true-by-plan or true-in-state

    def achieve(pred: Pred, working: FrozenSet) -> bool:
        if pred.holds(state):
            return True
        k = pred_key(pred)
        if k in achieved:
            return True
        if k in working:
            return False  # cycle - this branch cannot close
        working = working | {k}
        for schema, bindings in G.achievers(pred):
            ga = ground(schema, bindings)
            ok = True
            for p in ga.pre:
                if not achieve(p, working):
                    ok = False
                    break
            if not ok:
                continue
            if len(plan) >= max_actions:
                return False
            plan.append(ga)
            achieved.add(k)
            for at in ga.add:
                achieved.add(pred_key(at))
            for var, value in ga.sets:
                achieved.add(("thr_set", var, value))
            return True
        return False

    for g in goals:
        if not achieve(g, frozenset()):
            return None
    return plan


# --------------------------------------------------------------------------- #
# Validation (forward simulation under G)
# --------------------------------------------------------------------------- #
def validate_plan(plan: Sequence[GroundAction], goals: Sequence[Pred], state: State) -> bool:
    """True iff executing ``plan`` from ``state`` (under G's own model) is
    precondition-consistent and achieves every goal."""
    s = state.copy()
    for ga in plan:
        for p in ga.pre:
            if not p.holds(s):
                return False
        s.apply(ga)
    return all(g.holds(s) for g in goals)


# --------------------------------------------------------------------------- #
# Forward BFS fallback
# --------------------------------------------------------------------------- #
def _all_ground_actions(G: CausalGraph, goals: Sequence[Pred]) -> List[GroundAction]:
    """Enumerate a finite candidate set of ground actions.

    Parameter-free actions are grounded directly. Parametric (navigation)
    actions are grounded against the threshold values that appear in the goal or
    in any schema precondition - the only values that can matter for reaching a
    gate.
    """
    targets: Dict[str, set] = {}
    preds: List[Pred] = list(goals)
    for a in G.actions:
        preds.extend(a.pre)
    for p in preds:
        if isinstance(p, Threshold):
            targets.setdefault(p.var, set()).add(p.value)

    out: List[GroundAction] = []
    for a in G.actions:
        if not a.params:
            out.append(ground(a, {}))
            continue
        # parametric: bind the (single) param via each set-effect target var
        candidate_values = set()
        for var, src in a.sets:
            for v in targets.get(var, set()):
                candidate_values.add(v)
        for v in candidate_values:
            param = a.params[0]
            out.append(ground(a, {param: v}))
    return out


def bfs_plan(
    goals: Sequence[Pred],
    G: CausalGraph,
    state: State,
    max_depth: int = 16,
) -> Optional[List[GroundAction]]:
    """Bounded breadth-first forward search. Returns the shortest valid plan."""
    actions = _all_ground_actions(G, goals)

    def goal_met(s: State) -> bool:
        return all(g.holds(s) for g in goals)

    if goal_met(state):
        return []

    seen = {_state_key(state)}
    frontier: deque = deque([(state, [])])
    while frontier:
        s, acts = frontier.popleft()
        if len(acts) >= max_depth:
            continue
        for ga in actions:
            if not all(p.holds(s) for p in ga.pre):
                continue
            ns = s.copy()
            ns.apply(ga)
            key = _state_key(ns)
            if key in seen:
                continue
            nacts = acts + [ga]
            if goal_met(ns):
                return nacts
            seen.add(key)
            frontier.append((ns, nacts))
    return None


def _state_key(s: State) -> Tuple:
    return (frozenset(s.atoms), tuple(sorted(s.nums.items())))


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def plan(goals: Sequence[Pred], G: CausalGraph, state: State) -> Optional[List[GroundAction]]:
    """Plan with regression, validate, and fall back to BFS if needed."""
    p = regress_plan(goals, G, state)
    if p is not None and validate_plan(p, goals, state):
        return p
    return bfs_plan(goals, G, state)
