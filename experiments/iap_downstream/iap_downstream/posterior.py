"""Dual-pool Beta posterior - the "verify" decision of the Figure-2 loop (§4.5).

A candidate gate is tested by a *two-sided contrast*: interventions that make the
candidate predicate **true** feed the positive pool, interventions that make it
**false** feed the negative pool. We accept the candidate only when the
posterior probability that the success-rate *contrast* exceeds ``delta`` clears
``tau_acc`` **and** we have at least ``n_min`` effective observations on each
side. This is what stops the loop from writing back a predicate that merely
*correlates* with success (its negative side would also succeed).

``mode="point"`` is the ``-dual_pool`` ablation: it ignores the negative pool and
accepts on the positive empirical rate alone (see paper §6.3.2 / Appendix J).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class DualPool:
    """Beta-Bernoulli evidence for one candidate gate."""

    delta: float = 0.3
    tau_acc: float = 0.9
    tau_rej: float = 0.1
    n_min: int = 4
    n_mc: int = 4000
    mode: str = "dual"  # "dual" | "point"

    n_pos: int = 0
    k_pos: int = 0
    n_neg: int = 0
    k_neg: int = 0

    def update(self, side: str, success: bool) -> None:
        if side == "pos":
            self.n_pos += 1
            self.k_pos += int(success)
        elif side == "neg":
            self.n_neg += 1
            self.k_neg += int(success)
        else:  # pragma: no cover
            raise ValueError(side)

    @property
    def n_eff(self) -> int:
        return min(self.n_pos, self.n_neg)

    def q_hat(self, rng: random.Random) -> float:
        """Monte-Carlo estimate of P(theta_pos - theta_neg > delta)."""
        a_pos, b_pos = 1 + self.k_pos, 1 + (self.n_pos - self.k_pos)
        a_neg, b_neg = 1 + self.k_neg, 1 + (self.n_neg - self.k_neg)
        hits = 0
        for _ in range(self.n_mc):
            tp = rng.betavariate(a_pos, b_pos)
            tn = rng.betavariate(a_neg, b_neg)
            if tp - tn > self.delta:
                hits += 1
        return hits / self.n_mc

    def decide(self, rng: random.Random) -> str:
        """Return 'accepted' | 'rejected' | 'pending'."""
        if self.mode == "point":
            # -dual_pool ablation: positive side only, ignore the contrast.
            if self.n_pos < self.n_min:
                return "pending"
            p = self.k_pos / self.n_pos
            if p >= self.tau_acc:
                return "accepted"
            if p <= self.tau_rej:
                return "rejected"
            return "pending"
        # dual (default): require both sides + posterior contrast.
        if self.n_eff < self.n_min:
            return "pending"
        q = self.q_hat(rng)
        if q >= self.tau_acc:
            return "accepted"
        if q <= self.tau_rej:
            return "rejected"
        return "pending"
