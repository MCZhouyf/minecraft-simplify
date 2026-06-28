"""A small, deterministic MC-Drift-style mock environment + belief graphs.

Two situational tasks, each with a drift-injected gate:

* ``craftBoat``   (paper C1): in ``drift`` ``craft_boat`` requires being next to
  water (``water_radius <= 3``); a boolean-style gate that ``move_to_water``
  satisfies regardless of the exact discovered radius.
* ``mineDiamond`` (paper C3): in ``drift`` ``mine_diamond`` requires depth
  (``y_level <= -8`` operationally); a *parameter-sensitive* gate - descending
  to the wrong depth fails, which is how parameter precision shows up downstream.

This file is **only** for the self-test / demo. Against the real simulator you
implement :class:`~iap_downstream.env_adapter.Env` and load real CCG JSON into
:class:`~iap_downstream.causal_graph.CausalGraph`; nothing else changes.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from .causal_graph import Action, Atom, CausalGraph, GroundAction, State, Threshold, ground
from .env_adapter import Env, StepResult

# Operational (true, executable) gate values used by the world under drift.
WORLD_WATER_GATE = Threshold("water_radius", "<=", 3)
WORLD_DEPTH_GATE = Threshold("y_level", "<=", -8)   # operational boundary

GOALS = {
    "craftBoat": (Atom("have", ("boat",)),),
    "mineDiamond": (Atom("have", ("diamond",)),),
}

PAPER_ID = {"craftBoat": "C1", "mineDiamond": "C3"}


# --------------------------------------------------------------------------- #
# Shared (non-gated) action schemas
# --------------------------------------------------------------------------- #
def _gather_planks() -> Action:
    return Action("gather_planks", add=(Atom("have", ("planks",)),), cost=1.0)


def _move_to_water() -> Action:
    return Action("move_to_water", sets=(("water_radius", 0),), cost=2.0)


def _craft_pickaxe() -> Action:
    return Action("craft_pickaxe", add=(Atom("have", ("pickaxe",)),), cost=1.0)


def _descend() -> Action:
    return Action("descend", params=("target",), sets=(("y_level", "target"),), cost=2.0)


def _craft_boat(pre: Sequence) -> Action:
    return Action("craft_boat", pre=tuple(pre), add=(Atom("have", ("boat",)),), cost=1.0)


def _mine_diamond(pre: Sequence) -> Action:
    return Action("mine_diamond", pre=tuple(pre), add=(Atom("have", ("diamond",)),), cost=1.0)


# --------------------------------------------------------------------------- #
# The world (true model)
# --------------------------------------------------------------------------- #
class MockMCDrift(Env):
    def __init__(self, task: str, condition: str, seed: int = 0):
        assert task in GOALS, f"unknown task {task!r}"
        assert condition in ("origin", "drift"), condition
        self.task = task
        self.condition = condition
        self.seed = seed
        self.world = State(atoms=set(), nums={"y_level": 0, "water_radius": 99})
        self._world_actions: Dict[str, Action] = self._build_world_actions()

    # -- Env interface ----------------------------------------------------- #
    def reset(self, task: str, condition: str, seed: int) -> State:
        self.__init__(task, condition, seed)
        return self.snapshot()

    def step(self, ga: GroundAction) -> StepResult:
        schema = self._world_actions.get(ga.name)
        if schema is None:
            return StepResult(False, f"no world action {ga.name!r}")
        bindings = {k: v for k, v in ga.bindings}
        world_ga = ground(schema, bindings)
        for p in world_ga.pre:
            if not p.holds(self.world):
                return StepResult(False, f"unmet world precondition {p}")
        self.world.apply(world_ga)
        return StepResult(True)

    def holds(self, pred) -> bool:
        return pred.holds(self.world)

    def snapshot(self) -> State:
        # Fully observable in the mock; reveals state vars (depth, proximity) but
        # never the hidden precondition itself - that lives only in the belief G.
        return self.world.copy()

    def goal_of(self, task: str) -> Sequence:
        return GOALS[task]

    # -- discovery-half hooks --------------------------------------------- #
    def probe(self, assignments: Dict, action_name: str) -> bool:
        """Controlled experiment: apply ``assignments`` on top of the current
        world state, test whether ``action_name``'s true world-preconditions
        hold, and restore. Effects are NOT applied (pure feasibility test)."""
        tmp = self.world.copy()
        for var, val in assignments.items():
            if isinstance(val, bool):
                if val:
                    tmp.atoms.add((var, ()))
                else:
                    tmp.atoms.discard((var, ()))
            else:
                tmp.nums[var] = val
        schema = self._world_actions.get(action_name)
        if schema is None:
            return False
        return all(p.holds(tmp) for p in schema.pre)

    def signatures(self) -> Sequence[Dict]:
        """Enumerable situational gate templates the world can express."""
        return [
            {
                "target": "nearby_block", "property": "water", "kind": "num",
                "var": "water_radius", "comparator": "<=",
                "true_set": {"water_radius": 0}, "false_set": {"water_radius": 99},
                "probe_values": [0, 1, 2, 3, 4, 6], "achiever": "move_to_water", "cost": 2.0,
            },
            {
                "target": "y_level", "property": "depth", "kind": "num",
                "var": "y_level", "comparator": "<=",
                "true_set": {"y_level": -12}, "false_set": {"y_level": 0},
                "probe_values": [0, -3, -5, -8, -10, -12], "achiever": "descend", "cost": 2.5,
            },
        ]

    # -- world construction ------------------------------------------------ #
    def _build_world_actions(self) -> Dict[str, Action]:
        gated = self.condition == "drift"
        boat_pre: List = [Atom("have", ("planks",))]
        mine_pre: List = [Atom("have", ("pickaxe",))]
        if gated:
            boat_pre.append(WORLD_WATER_GATE)
            mine_pre.append(WORLD_DEPTH_GATE)
        actions = [
            _gather_planks(),
            _move_to_water(),
            _craft_pickaxe(),
            _descend(),
            _craft_boat(boat_pre),
            _mine_diamond(mine_pre),
        ]
        return {a.name: a for a in actions}


def mock_env_factory(task: str, condition: str, seed: int) -> MockMCDrift:
    return MockMCDrift(task, condition, seed)


# --------------------------------------------------------------------------- #
# Belief graphs (what each "method" plans with)
# --------------------------------------------------------------------------- #
def _base_actions() -> List[Action]:
    return [_gather_planks(), _move_to_water(), _craft_pickaxe(), _descend()]


def build_mock_graphs() -> Dict[str, CausalGraph]:
    """Construct the belief graphs for before/after/oracle/ablations."""

    def graph(boat_pre, mine_pre) -> CausalGraph:
        return CausalGraph(
            tuple(_base_actions() + [_craft_boat(boat_pre), _mine_diamond(mine_pre)])
        )

    planks = Atom("have", ("planks",))
    pickaxe = Atom("have", ("pickaxe",))

    return {
        # ground truth (nominal threshold -10; deep enough to satisfy world -8)
        "oracle": graph(
            [planks, Threshold("water_radius", "<=", 3)],
            [pickaxe, Threshold("y_level", "<=", -10)],
        ),
        # IaP writeback (operational threshold -8; matches the world)
        "after": graph(
            [planks, Threshold("water_radius", "<=", 3)],
            [pickaxe, Threshold("y_level", "<=", -8)],
        ),
        # stale graph: gates unknown -> plans omit the gate sub-goals
        "before": graph([planks], [pickaxe]),
        # -boundary intervention: structure present, threshold WRONG (too shallow)
        "minus_boundary": graph(
            [planks, Threshold("water_radius", "<=", 3)],
            [pickaxe, Threshold("y_level", "<=", -5)],
        ),
        # -NOTA: situational gate structure never proposed -> like 'before'
        "minus_nota": graph([planks], [pickaxe]),
    }


def mock_tasks() -> List:
    return [(PAPER_ID[t], t) for t in ("craftBoat", "mineDiamond")]
