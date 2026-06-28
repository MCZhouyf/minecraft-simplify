"""The NOTA tail of Figure 2 (§4.4).

When every LLM-proposed candidate is rejected ("none-of-the-above"), NOTA does
not give up: it enumerates the *signatures* the world can express (depth,
proximity, tier, count ...) and turns each into a candidate to verify. This
lifts the recall ceiling from "what the LLM thought of" to "what the signature
grammar can express" - which is how craftBoat's situational water gate gets
found after the resource candidates fail.
"""
from __future__ import annotations

from typing import List, Sequence

from .proposer import Candidate


def enumerate_candidates(action: str, signatures: Sequence[dict]) -> List[Candidate]:
    """Turn enumerable world signatures into candidates for ``action``."""
    out: List[Candidate] = []
    for sig in signatures:
        out.append(
            Candidate(
                action=action,
                label=f"nota::{sig.get('target', sig['var'])}.{sig.get('property', '')}",
                kind=sig.get("kind", "num"),
                var=sig["var"],
                comparator=sig.get("comparator", "<="),
                true_set=dict(sig["true_set"]),
                false_set=dict(sig["false_set"]),
                probe_values=tuple(sig.get("probe_values", ())),
                achiever=sig.get("achiever", ""),
                cost=float(sig.get("cost", 2.0)),
                source="nota",
            )
        )
    return out
