"""Core data model for the downstream-success harness.

A *causal graph* ``G`` is the agent's belief about how the world works: a set of
action schemas, each with preconditions, add/delete effects and (for navigation)
numeric set-effects. The planner uses ``G`` to build a plan. The *environment*
holds the **true** world model (which in the ``drift`` condition contains extra
"gate" preconditions). The whole point of the harness is to compare downstream
task success when the planner is handed different ``G``:

* ``G_before``  - stale graph, missing the drift-introduced gates.
* ``G_after``   - what IaP discovered + wrote back (gates present, operational
                  threshold values).
* ``G_oracle``  - ground-truth graph (nominal gates).
* ablation graphs (e.g. wrong threshold values, missing structure).

Predicates come in two flavours:

* :class:`Atom`      - a boolean fact, e.g. ``Atom("have", ("boat",))``.
* :class:`Threshold` - a numeric condition, e.g. ``Threshold("y_level", "<=", -10)``.

The model is intentionally small and *deterministic*; it is verified by the test
suite. To use it against the real MC-Drift environment you only implement
:class:`~iap_downstream.env_adapter.Env` (see ``env_adapter.py``); the planner,
executor, harness and metrics are environment-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple, Union

Number = Union[int, float]

_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
}


def _cmp(x: Number, op: str, value: Number) -> bool:
    try:
        return _OPS[op](x, value)
    except KeyError as exc:  # pragma: no cover - guards programmer error
        raise ValueError(f"unknown comparison operator {op!r}") from exc


# --------------------------------------------------------------------------- #
# Predicates
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Atom:
    """A boolean fact such as ``have(boat)`` or ``near_water``."""

    name: str
    args: Tuple = ()

    def holds(self, state: "State") -> bool:
        return (self.name, self.args) in state.atoms

    def key(self) -> Tuple:
        return ("atom", self.name, self.args)


@dataclass(frozen=True)
class Threshold:
    """A numeric condition such as ``y_level <= -10``."""

    var: str
    op: str
    value: Number

    def holds(self, state: "State") -> bool:
        if self.var not in state.nums:
            return False
        return _cmp(state.nums[self.var], self.op, self.value)

    def key(self) -> Tuple:
        # NB: value is part of the key on purpose - "y<=-8" and "y<=-10" are
        # genuinely different sub-goals, which is how parameter precision feeds
        # into downstream success.
        return ("thr", self.var, self.op, self.value)


Pred = Union[Atom, Threshold]


def pred_key(p: Pred) -> Tuple:
    return p.key()


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Action:
    """An action *schema*.

    ``params`` names the free parameters (only navigation uses one, ``target``).
    ``sets`` is a tuple of ``(var, source)`` numeric assignments where ``source``
    is either a parameter name (bound at grounding time) or a literal number.
    Preconditions and add/delete effects are parameter-free in this model.
    """

    name: str
    params: Tuple[str, ...] = ()
    pre: Tuple[Pred, ...] = ()
    add: Tuple[Atom, ...] = ()
    delete: Tuple[Atom, ...] = ()
    sets: Tuple[Tuple[str, Union[str, Number]], ...] = ()
    cost: float = 1.0


@dataclass(frozen=True)
class GroundAction:
    """A fully bound action ready to validate or execute."""

    name: str
    bindings: Tuple[Tuple[str, Number], ...] = ()  # sorted (param, value) pairs
    pre: Tuple[Pred, ...] = ()
    add: Tuple[Atom, ...] = ()
    delete: Tuple[Atom, ...] = ()
    sets: Tuple[Tuple[str, Number], ...] = ()
    cost: float = 1.0

    def binding(self, name: str) -> Optional[Number]:
        for k, v in self.bindings:
            if k == name:
                return v
        return None


def _resolve(source: Union[str, Number], bindings: Dict[str, Number]) -> Number:
    if isinstance(source, (int, float)):
        return source
    if source in bindings:
        return bindings[source]
    raise ValueError(f"cannot resolve set-source {source!r} (bindings={bindings})")


def ground(schema: Action, bindings: Optional[Dict[str, Number]] = None) -> GroundAction:
    """Bind a schema's parameters, producing a concrete :class:`GroundAction`."""

    bindings = dict(bindings or {})
    missing = [p for p in schema.params if p not in bindings]
    if missing:
        raise ValueError(f"action {schema.name!r} missing bindings for {missing}")
    sets = tuple((var, _resolve(src, bindings)) for var, src in schema.sets)
    return GroundAction(
        name=schema.name,
        bindings=tuple(sorted(bindings.items())),
        pre=tuple(schema.pre),
        add=tuple(schema.add),
        delete=tuple(schema.delete),
        sets=sets,
        cost=schema.cost,
    )


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
@dataclass
class State:
    """A symbolic world / belief state: a set of true atoms plus numeric vars."""

    atoms: set = field(default_factory=set)  # set of (name, args)
    nums: Dict[str, Number] = field(default_factory=dict)

    def copy(self) -> "State":
        return State(atoms=set(self.atoms), nums=dict(self.nums))

    def holds(self, pred: Pred) -> bool:
        return pred.holds(self)

    def apply(self, ga: GroundAction) -> None:
        """Apply a ground action's effects in place (delete, then add, then set)."""
        for at in ga.delete:
            self.atoms.discard((at.name, at.args))
        for at in ga.add:
            self.atoms.add((at.name, at.args))
        for var, value in ga.sets:
            self.nums[var] = value


# --------------------------------------------------------------------------- #
# Causal graph
# --------------------------------------------------------------------------- #
@dataclass
class CausalGraph:
    """The planner's belief: a set of action schemas."""

    actions: Tuple[Action, ...]

    def achievers(self, pred: Pred) -> List[Tuple[Action, Dict[str, Number]]]:
        """Return ``(schema, bindings)`` candidates that can achieve ``pred``.

        For an :class:`Atom`, an achiever is any action that adds it. For a
        :class:`Threshold` ``var op value``, an achiever is any action with a
        set-effect on ``var``; we bind its source parameter to ``value`` so that
        executing it makes ``var == value`` (which satisfies non-strict ``<=`` /
        ``>=`` gates used by MC-Drift).
        """
        out: List[Tuple[Action, Dict[str, Number]]] = []
        for a in self.actions:
            if isinstance(pred, Atom):
                if pred in a.add:
                    out.append((a, {}))
            elif isinstance(pred, Threshold):
                for var, src in a.sets:
                    if var == pred.var:
                        bindings: Dict[str, Number] = {}
                        if isinstance(src, str):
                            bindings[src] = pred.value
                        out.append((a, bindings))
        # Prefer cheaper achievers for determinism / cost-awareness.
        out.sort(key=lambda ab: ab[0].cost)
        return out

    def schema(self, name: str) -> Optional[Action]:
        for a in self.actions:
            if a.name == name:
                return a
        return None

    def add_gate(self, action_name: str, gate: Pred) -> "CausalGraph":
        """Return a new graph with ``gate`` added as a precondition of
        ``action_name`` (the write-back step of Figure 2). The achiever action
        for the gate must already exist in the graph."""
        new_actions = []
        for a in self.actions:
            if a.name == action_name and gate not in a.pre:
                a = Action(
                    name=a.name,
                    params=a.params,
                    pre=tuple(a.pre) + (gate,),
                    add=a.add,
                    delete=a.delete,
                    sets=a.sets,
                    cost=a.cost,
                )
            new_actions.append(a)
        return CausalGraph(tuple(new_actions))
