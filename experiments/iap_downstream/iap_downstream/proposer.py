"""The *propose* step of Figure 2 (§4.2).

A :class:`Proposer` turns a failed action into typed candidate gates. The LLM
prior tends to propose **resource**-type candidates first (more inputs, a
missing tool); these are spurious for situational drift and get rejected by the
verifier, which is exactly what triggers the NOTA tail.

``MockProposer`` is a deterministic stand-in for the self-test. Implement
:class:`Proposer.propose` against your real LLM to wire the true pipeline; the
return type (:class:`Candidate`) is all the rest of the loop needs.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .causal_graph import State


@dataclass
class Candidate:
    """A typed candidate gate to be verified by intervention.

    ``true_set`` / ``false_set`` are world assignments that make the candidate
    predicate true / false (used for the two-sided contrast). ``probe_values``
    are the numeric thresholds tried during the boundary intervention.
    ``achiever`` is the planner action that can satisfy the gate downstream (it
    must already exist in the causal graph).
    """

    action: str
    label: str
    kind: str = "num"                 # "num" | "bool"
    var: str = ""
    comparator: str = "<="
    value: Optional[float] = None     # filled by the boundary intervention
    true_set: Dict[str, float] = field(default_factory=dict)
    false_set: Dict[str, float] = field(default_factory=dict)
    probe_values: Tuple[float, ...] = ()
    achiever: str = ""
    cost: float = 1.0
    source: str = "llm"               # "llm" | "nota"


class Proposer(abc.ABC):
    @abc.abstractmethod
    def propose(self, action: str, observable: State) -> List[Candidate]:
        """Return typed candidate gates for a failed ``action``."""


class MockProposer(Proposer):
    """Deterministic resource-first proposer used by the self-test."""

    def propose(self, action: str, observable: State) -> List[Candidate]:
        if action == "craft_boat":
            return [
                Candidate(action, "extra_planks", var="extra_planks", comparator=">=",
                          true_set={"extra_planks": 9}, false_set={"extra_planks": 0},
                          probe_values=(1, 3, 9), achiever="gather_planks", cost=1.0),
                Candidate(action, "crafting_table", var="has_table", comparator=">=",
                          true_set={"has_table": 1}, false_set={"has_table": 0},
                          probe_values=(1,), achiever="make_table", cost=1.0),
            ]
        if action == "mine_diamond":
            return [
                Candidate(action, "better_pickaxe", var="pickaxe_tier", comparator=">=",
                          true_set={"pickaxe_tier": 9}, false_set={"pickaxe_tier": 0},
                          probe_values=(1, 5, 9), achiever="craft_pickaxe", cost=1.0),
            ]
        return []
