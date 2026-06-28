"""The causal-calibration closed loop of Figure 2 (§4.2-4.5, §4.8 NOTA).

Given a failed action, this:

1. asks the proposer for typed candidate gates (LLM prior, resource-ish first),
2. **verifies** each with a two-sided contrast fed into a :class:`DualPool`
   (interventions that make the candidate true vs false), accepting only on a
   real positive-vs-negative contrast,
3. on accept, runs a **boundary intervention** to pin the numeric threshold,
4. if *all* candidates are rejected, triggers **NOTA** signature enumeration and
   verifies those,
5. returns the discovered gate(s) to **write back**; trajectories that never
   clear the posterior change nothing.

It returns a :class:`CalibrationResult` (the gate to write back, plus accounting
of how many interventions were spent) - it does **not** mutate the graph itself;
the agent does the write-back so the ledger stays explicit.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .acquisition import order
from .causal_graph import Pred, Threshold
from .env_adapter import Env
from .nota import enumerate_candidates
from .posterior import DualPool
from .proposer import Candidate, Proposer


@dataclass
class CalibrationResult:
    gate: Optional[Pred]          # Threshold to write back, or None
    achiever: str = ""
    interventions: int = 0
    accepted_label: str = ""
    used_nota: bool = False
    tested: List[Tuple[str, str]] = field(default_factory=list)  # (label, decision)


def _verify(
    cand: Candidate,
    env: Env,
    cfg: dict,
    rng: random.Random,
    max_rounds: int,
) -> Tuple[str, int]:
    """Two-sided contrast verification. Returns (decision, n_interventions)."""
    pool = DualPool(**cfg)
    n = 0
    for _ in range(max_rounds):
        pool.update("pos", env.probe(cand.true_set, cand.action))
        pool.update("neg", env.probe(cand.false_set, cand.action))
        n += 2
        d = pool.decide(rng)
        if d != "pending":
            return d, n
    return pool.decide(rng), n


def _boundary(cand: Candidate, env: Env) -> Tuple[Optional[float], int]:
    """Boundary intervention: find the extreme threshold value that still makes
    the action succeed. Returns (value, n_interventions)."""
    if cand.kind != "num" or not cand.probe_values:
        return cand.value, 0
    n = 0
    ok_values: List[float] = []
    for v in cand.probe_values:
        success = env.probe({cand.var: v}, cand.action)
        n += 1
        if success:
            ok_values.append(v)
    if not ok_values:
        return None, n
    # for "<=" the gate is the largest still-succeeding value; for ">=" smallest.
    if cand.comparator == "<=":
        return max(ok_values), n
    if cand.comparator == ">=":
        return min(ok_values), n
    return ok_values[0], n


def calibrate(
    failed_action: str,
    env: Env,
    proposer: Proposer,
    *,
    posterior_cfg: Optional[dict] = None,
    cost_alpha: float = 1.0,
    max_rounds: int = 8,
    seed: int = 0,
) -> CalibrationResult:
    """Run the propose -> verify -> (NOTA) -> boundary -> write-back loop."""
    rng = random.Random(seed)
    cfg = dict(delta=0.3, tau_acc=0.9, tau_rej=0.1, n_min=4, n_mc=4000, mode="dual")
    if posterior_cfg:
        cfg.update(posterior_cfg)

    result = CalibrationResult(gate=None)

    def try_candidates(cands: List[Candidate], nota: bool) -> bool:
        for cand in order(cands, cost_alpha=cost_alpha):
            decision, n = _verify(cand, env, cfg, rng, max_rounds)
            result.interventions += n
            result.tested.append((cand.label, decision))
            if decision == "accepted":
                value, nb = _boundary(cand, env)
                result.interventions += nb
                if value is None:
                    continue
                result.gate = Threshold(cand.var, cand.comparator, value)
                result.achiever = cand.achiever
                result.accepted_label = cand.label
                result.used_nota = nota
                return True
        return False

    observable = env.snapshot()
    proposed = proposer.propose(failed_action, observable)
    if try_candidates(proposed, nota=False):
        return result

    # all proposed candidates rejected -> NOTA signature enumeration
    nota_cands = enumerate_candidates(failed_action, env.signatures())
    try_candidates(nota_cands, nota=True)
    return result
