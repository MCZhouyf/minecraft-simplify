"""Cost-aware acquisition (§4) - the order in which candidates are verified.

Cheaper-to-test, more-promising candidates first. ``cost_alpha=0`` recovers the
cost-blind ``-costaware`` ablation (paper §6.3.2). Kept deliberately small; the
mock tasks have few candidates so ordering rarely changes the outcome, only the
number of interventions spent (which is the metric the ablation targets).
"""
from __future__ import annotations

from typing import List, Sequence

from .proposer import Candidate


def order(candidates: Sequence[Candidate], cost_alpha: float = 1.0) -> List[Candidate]:
    """Return candidates ordered by ascending cost (LLM-proposed before NOTA)."""
    src_rank = {"llm": 0, "nota": 1}
    if cost_alpha == 0.0:
        # cost-blind: keep proposal order, only LLM-before-NOTA.
        return sorted(candidates, key=lambda c: src_rank.get(c.source, 2))
    return sorted(
        candidates,
        key=lambda c: (src_rank.get(c.source, 2), cost_alpha * c.cost),
    )
