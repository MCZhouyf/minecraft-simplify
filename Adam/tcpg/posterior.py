"""Stage 6: dual-pool Beta posterior + acquisition (paper Sec. 4.4, App. D).

Each candidate (h, a) keeps two pools of (executed a | h=side) observations.
With Beta(1,1) priors the posteriors are analytic; q_hat and gamma_plus are
estimated from ONE shared batch of Monte-Carlo posterior draws.

Decision rule (write-back gate):
  accepted  iff q_hat >= tau_acc and n_eff >= n_min
  rejected  iff q_hat <= tau_rej and n_eff >= n_min
  undecided otherwise
Acquisition: greedy on q_hat * gamma_plus with a ROUND-ROBIN FLOOR (every
`rr_every`-th pick takes the least-observed undecided candidate) — the floor
is what makes Theorem 1's pigeonhole guarantee unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

DEFAULTS = {"delta": 0.3, "tau_acc": 0.9, "tau_rej": 0.1,
            "n_min": 4, "M": 100_000, "rr_every": 3,
            "cost_alpha": 0.5, "cost_c0": 1.0}


@dataclass
class DualPool:
    n_pos: int = 0
    k_pos: int = 0
    n_neg: int = 0
    k_neg: int = 0
    _last: Optional[Tuple[str, int]] = field(default=None, repr=False)

    # ------------------------------------------------------------- updates
    def update(self, side: str, y: int) -> None:
        y = int(bool(y))
        if side == "pos":
            self.n_pos += 1
            self.k_pos += y
        elif side == "neg":
            self.n_neg += 1
            self.k_neg += y
        else:
            raise ValueError(f"bad side {side!r}")
        self._last = (side, y)

    def invalidate_last(self) -> bool:
        """Undo failed / context drifted: the most recent observation is void
        (clean-discard contract from stage 5). Returns False if nothing to void."""
        if self._last is None:
            return False
        side, y = self._last
        if side == "pos":
            self.n_pos -= 1
            self.k_pos -= y
        else:
            self.n_neg -= 1
            self.k_neg -= y
        self._last = None
        return True

    # ------------------------------------------------------------- posterior
    @property
    def n_eff(self) -> int:
        return min(self.n_pos, self.n_neg)

    def stats(self, delta: float = DEFAULTS["delta"], M: int = DEFAULTS["M"],
              seed: Optional[int] = None) -> Tuple[float, float]:
        """(q_hat, gamma_plus) from one shared MC batch.
        q_hat = P(theta+ - theta- > delta | pools); gamma_plus = E[max(diff,0)]."""
        rng = np.random.default_rng(seed)
        tp = rng.beta(1 + self.k_pos, 1 + self.n_pos - self.k_pos, M)
        tm = rng.beta(1 + self.k_neg, 1 + self.n_neg - self.k_neg, M)
        diff = tp - tm
        return float(np.mean(diff > delta)), float(np.mean(np.maximum(diff, 0.0)))

    def decide(self, delta: float = DEFAULTS["delta"],
               tau_acc: float = DEFAULTS["tau_acc"],
               tau_rej: float = DEFAULTS["tau_rej"],
               n_min: int = DEFAULTS["n_min"],
               M: int = DEFAULTS["M"], seed: Optional[int] = None,
               mode: str = "dual") -> str:
        if mode == "point":
            if self.n_pos >= n_min:
                p = self.k_pos / max(self.n_pos, 1)
                if p >= tau_acc:
                    return "accepted"
                if p <= tau_rej:
                    return "rejected"
            return "pending"
        if mode != "dual":
            raise ValueError(f"bad posterior mode {mode!r}")
        q, _ = self.stats(delta=delta, M=M, seed=seed)
        if self.n_eff >= n_min:
            if q >= tau_acc:
                return "accepted"
            if q <= tau_rej:
                return "rejected"
        return "undecided"


# ------------------------------------------------------------------ acquisition
class Acquisition:
    """Budget-aware acquisition. Score = q_hat * gamma_plus / (cost+c0)^alpha,
    with a deterministic round-robin floor (every rr_every picks) and an
    optional per-trigger budget that caps eligibility to affordable candidates.

    This restores the cost term from the original cost-constrained design
    (Score = q*ΔE / c): cheap interventions (e.g. moving a few blocks for a
    y_level check) are preferred over expensive ones (e.g. crafting a diamond
    pickaxe), so a correct-but-cheap true cause is verified first instead of
    being starved behind costly ordered-domain neighbours. cost=None or
    alpha=0 reproduces the original cost-blind q*gamma behaviour."""

    def __init__(self, rr_every: int = DEFAULTS["rr_every"],
                 delta: float = DEFAULTS["delta"], M: int = 20_000,
                 seed: int = 0, cost_alpha: float = DEFAULTS["cost_alpha"],
                 cost_c0: float = DEFAULTS["cost_c0"]):
        self.rr_every = max(2, int(rr_every))
        self.delta = delta
        self.M = M
        self.seed = seed
        self.cost_alpha = float(cost_alpha)
        self.cost_c0 = float(cost_c0)
        self._picks = 0
        self.counts: Dict[str, int] = {}

    def select(self, pools: Dict[str, DualPool], eligible: List[str],
               costs: Optional[Dict[str, float]] = None,
               budget: Optional[float] = None) -> Optional[str]:
        """eligible = undecided AND intervention-feasible cids.
        costs[cid] = per-intervention step cost (compiler est_steps); if given
        and budget is set, only candidates with cost<=budget are selectable.
        Falls back to cost-blind behaviour when costs is None or alpha==0."""
        pool_elig = eligible
        if costs is not None and budget is not None:
            affordable = [c for c in eligible if costs.get(c, 0.0) <= budget]
            pool_elig = affordable or eligible  # if none affordable, don't stall
        if not pool_elig:
            return None
        self._picks += 1
        if self._picks % self.rr_every == 0:        # round-robin floor
            cid = min(pool_elig, key=lambda c: (self.counts.get(c, 0), c))
        else:                                        # cost-aware greedy
            def score(c: str) -> float:
                q, g = pools[c].stats(delta=self.delta, M=self.M,
                                      seed=self.seed + self.counts.get(c, 0))
                base = q * g
                if costs is None or self.cost_alpha == 0.0:
                    return base
                cost = max(0.0, float(costs.get(c, 0.0)))
                return base / ((cost + self.cost_c0) ** self.cost_alpha)
            cid = max(pool_elig, key=score)
        self.counts[cid] = self.counts.get(cid, 0) + 1
        return cid


def scarce_side(pool: DualPool) -> str:
    """Active intervention fills the thinner pool (pos ties win)."""
    return "pos" if pool.n_pos <= pool.n_neg else "neg"
