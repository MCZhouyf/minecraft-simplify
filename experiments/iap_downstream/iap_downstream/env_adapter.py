"""Environment interface for the downstream harness.

This is the **only** seam you implement to run against the real MC-Drift
environment. The harness never touches the simulator directly; it only calls the
five methods below. ``mock_env.MockMCDrift`` is a reference implementation used
by the tests.

Contract
--------
* ``reset(task, condition, seed)`` puts the world in its initial state with an
  **empty inventory** and the given drift ``condition`` ("origin" | "drift").
* ``step(ground_action)`` attempts the action against the **true** world model
  (which, under "drift", enforces the injected gate preconditions). It returns a
  :class:`StepResult` whose ``ok`` flag says whether the action's preconditions
  held and effects were applied. Crucially, ``step`` uses the *world's* model,
  not the planner's belief - so a plan built from a stale ``G`` will issue
  actions that fail here.
* ``holds(pred)`` evaluates a predicate against the current **world** state
  (used for goal checking).
* ``snapshot()`` returns the agent's **observable** symbolic state as a
  :class:`~iap_downstream.causal_graph.State` (fed back to the planner on
  replan). Observability may be partial; it must never leak the hidden gate as a
  belief - only what the agent can actually sense.
* ``goal_of(task)`` returns the goal predicates for the task.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import List, Sequence

from .causal_graph import GroundAction, Pred, State


@dataclass
class StepResult:
    ok: bool                 # did the action's world-preconditions hold?
    info: str = ""           # optional human-readable reason on failure


class Env(abc.ABC):
    """Abstract MC-Drift-style environment."""

    @abc.abstractmethod
    def reset(self, task: str, condition: str, seed: int) -> State:
        """Reset to an empty-inventory initial state. Returns the observable state."""

    @abc.abstractmethod
    def step(self, ga: GroundAction) -> StepResult:
        """Attempt ``ga`` against the true world model."""

    @abc.abstractmethod
    def holds(self, pred: Pred) -> bool:
        """Evaluate ``pred`` against the current world state."""

    @abc.abstractmethod
    def snapshot(self) -> State:
        """Return the agent's currently observable symbolic state."""

    @abc.abstractmethod
    def goal_of(self, task: str) -> Sequence[Pred]:
        """Return the goal predicates of ``task``."""

    # ----- discovery-half hooks (Figure-2 calibration loop) --------------- #
    # Only needed by the full IaP agent (agent.py); Stage-B-only adapters may
    # leave these unimplemented.
    def probe(self, assignments: "dict", action_name: str) -> bool:
        """Controlled experiment for the *verify* step.

        Temporarily set the given numeric/boolean ``assignments`` (the
        intervention), attempt ``action_name`` against the **true world model**,
        return whether it succeeded, then restore world state. This is how the
        two-sided contrast / boundary intervention gather +/- evidence without
        corrupting the task state. Cost (one intervention) is the caller's to
        account for.
        """
        raise NotImplementedError("env must implement probe() for the IaP loop")

    def signatures(self) -> "Sequence[dict]":
        """Return the enumerable gate signatures for the NOTA tail.

        Each signature is a template ``{"target", "property", "comparator",
        "kind": "bool"|"num", "var", "achiever"}`` describing a gate the world
        *could* express, so NOTA can enumerate beyond what the LLM proposed.
        """
        raise NotImplementedError("env must implement signatures() for NOTA")
