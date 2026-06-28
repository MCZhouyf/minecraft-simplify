"""MC-Drift downstream environment adapter.

This adapter exposes the MC-Drift task semantics through the small Env interface
used by ``iap_downstream``.  It deliberately keeps learning/discovery out of the
loop: the world enforces the injected drift gates, while the planner only sees
the frozen graph passed by the harness.
"""
from __future__ import annotations

import random
import copy
from dataclasses import dataclass
from typing import Dict, Sequence

from iap_downstream.causal_graph import Atom, GroundAction, Pred, State, Threshold
from iap_downstream.env_adapter import Env, StepResult

GOALS = {
    "craftFence": (Atom("have", ("oak_fence",)),),
    "gatherCoalOre": (Atom("have", ("coal",)),),
    "mineGoldOre": (Atom("have", ("raw_gold",)),),
    "craftBoat": (Atom("have", ("oak_boat",)),),
    "smeltRawIron": (Atom("have", ("iron_ingot",)),),
    "mineDiamondOre": (Atom("have", ("diamond",)),),
}

TASK_TO_BIAS = {
    "craftFence": "R2",
    "gatherCoalOre": "R5",
    "mineGoldOre": "R6",
    "craftBoat": "C2",
    "smeltRawIron": "C3",
    "mineDiamondOre": "C4",
}

WORLD_GATES = {
    "craftFence": Threshold("oak_planks_count", ">=", 8),
    "gatherCoalOre": Threshold("held_tool.tier", ">=", 1),
    "mineGoldOre": Threshold("held_tool.tier", ">=", 3),
    "craftBoat": Threshold("water_radius", "<=", 4),
    # Java gate currently accepts the manual nighttime definition.  The oracle
    # graph may use 18000 from [13000,23000]; both satisfy this world point.
    "smeltRawIron": Threshold("time_of_day", ">=", 12000),
    # Live gate smoke confirms y=-7/-8 fail and y<=-10 succeeds.
    "mineDiamondOre": Threshold("y_level", "<=", -10),
}

RECIPES: Dict[str, Dict] = {
    "gatherWoodLog": {"out": "oak_log", "inputs": {}},
    "craftPlanks": {"out": "oak_planks", "inputs": {"oak_log": 1}, "count": 4},
    "craftSticks": {"out": "stick", "inputs": {"oak_planks": 2}, "count": 4},
    "craftCraftingTable": {"out": "crafting_table", "inputs": {"oak_planks": 4}},
    "craftWoodenPickaxe": {"out": "wooden_pickaxe", "inputs": {"oak_planks": 3, "stick": 2}},
    "craftFence": {"out": "oak_fence", "inputs": {"oak_planks": 4, "stick": 2}},
    "gatherStone": {"out": "cobblestone", "inputs": {}},
    "craftStonePickaxe": {"out": "stone_pickaxe", "inputs": {"cobblestone": 3, "stick": 2}},
    "mineGoldOre": {"out": "raw_gold", "inputs": {}},
    "mineIronOre": {"out": "raw_iron", "inputs": {}},
    "gatherCoalOre": {"out": "coal", "inputs": {}},
    "smeltRawIron": {"out": "iron_ingot", "inputs": {"raw_iron": 1, "coal": 1}},
    "craftIronPickaxe": {"out": "iron_pickaxe", "inputs": {"iron_ingot": 3, "stick": 2}},
    "mineDiamondOre": {"out": "diamond", "inputs": {}},
    "craftBoat": {"out": "oak_boat", "inputs": {"oak_planks": 5}},
}

PICKAXE_TIER = {
    "wooden_pickaxe": 0,
    "stone_pickaxe": 1,
    "iron_pickaxe": 2,
    "diamond_pickaxe": 3,
}


@dataclass
class _World:
    inventory: Dict[str, int]
    nums: Dict[str, float]


class MCDriftDownstreamEnv(Env):
    """Small deterministic execution model for MC-Drift downstream episodes.

    ``condition='drift'`` enforces the real injected gates. ``condition='origin'``
    executes the same action vocabulary without those hidden gates.
    """

    def __init__(self, task: str, condition: str, seed: int = 0):
        self.reset(task, condition, seed)

    def reset(self, task: str, condition: str, seed: int) -> State:
        if task not in GOALS:
            raise ValueError(f"unknown downstream task {task!r}")
        if condition not in ("origin", "drift"):
            raise ValueError(f"unknown downstream condition {condition!r}")
        random.seed(seed)
        self.task = task
        self.condition = condition
        self.seed = seed
        self.world = _World(
            inventory={},
            nums={
                "y_level": 64.0,
                "time_of_day": 6000.0,
                "held_tool.tier": -1.0,
                "water_radius": 99.0,
                "extra_planks": 0.0,
                "oak_planks_count": 0.0,
                "has_table": 0.0,
                "pickaxe_tier": -1.0,
            },
        )
        return self.snapshot()

    def step(self, ga: GroundAction) -> StepResult:
        if ga.name == "descend":
            return self._set_num("y_level", ga)
        if ga.name == "set_time":
            return self._set_num("time_of_day", ga)
        if ga.name == "equip_tool":
            return self._set_num("held_tool.tier", ga)
        if ga.name == "approach_water":
            return self._set_num("water_radius", ga)
        if ga.name == "stock_oak_planks":
            value = ga.binding("target")
            if value is None and ga.sets:
                value = ga.sets[0][1]
            n = int(value or 0)
            self.world.inventory["oak_planks"] = max(self.world.inventory.get("oak_planks", 0), n)
            self.world.nums["oak_planks_count"] = float(self.world.inventory["oak_planks"])
            return StepResult(True)
        if ga.name not in RECIPES:
            return StepResult(False, f"no world action {ga.name!r}")
        spec = RECIPES[ga.name]
        # The downstream graph represents recipe inputs as boolean
        # have(item), not counted quantities.  Keep the world check aligned with
        # that abstraction; Stage A is where exact MC recipe counts are tested.
        missing = [item for item in spec.get("inputs", {})
                   if self.world.inventory.get(item, 0) <= 0]
        if missing:
            return StepResult(False, "missing inputs: " + ",".join(missing))
        if self.condition == "drift" and ga.name == self.task:
            gate = WORLD_GATES.get(ga.name)
            if gate and not gate.holds(self.snapshot()):
                return StepResult(False, f"unmet drift gate {gate}")
        for item in spec.get("inputs", {}):
            self.world.inventory[item] = max(0, self.world.inventory.get(item, 0) - 1)
        out = spec["out"]
        self.world.inventory[out] = self.world.inventory.get(out, 0) + int(spec.get("count", 1))
        if out == "oak_planks":
            self.world.nums["oak_planks_count"] = self.world.inventory.get("oak_planks", 0)
        if "oak_planks" in spec.get("inputs", {}):
            self.world.nums["oak_planks_count"] = self.world.inventory.get("oak_planks", 0)
        if out in PICKAXE_TIER:
            # Equipment is observable in MC-Drift; for the symbolic downstream
            # model, crafting the tool makes it available to equip/reach a tier.
            self.world.nums["held_tool.tier"] = max(self.world.nums.get("held_tool.tier", -1), PICKAXE_TIER[out])
            self.world.nums["pickaxe_tier"] = max(self.world.nums.get("pickaxe_tier", -1), PICKAXE_TIER[out])
        return StepResult(True)

    def holds(self, pred: Pred) -> bool:
        return pred.holds(self.snapshot())

    def snapshot(self) -> State:
        atoms = {("have", (item,)) for item, n in self.world.inventory.items() if n > 0}
        return State(atoms=atoms, nums=dict(self.world.nums))

    def goal_of(self, task: str) -> Sequence[Pred]:
        return GOALS[task]

    def probe(self, assignments: Dict, action_name: str) -> bool:
        saved = copy.deepcopy(self.world)
        try:
            for var, value in assignments.items():
                self.world.nums[str(var)] = float(value)
            if action_name in RECIPES:
                # Probe isolates the candidate gate, not mundane resource
                # acquisition; assume recipe resources for the failed action are
                # present exactly as the original failure observation did.
                for item in RECIPES[action_name].get("inputs", {}):
                    self.world.inventory[item] = max(1, self.world.inventory.get(item, 0))
            return self._world_preconditions_hold(action_name)
        finally:
            self.world = saved

    def signatures(self) -> Sequence[Dict]:
        return [
            {
                "target": "nearby_block",
                "property": "water",
                "kind": "num",
                "var": "water_radius",
                "comparator": "<=",
                "true_set": {"water_radius": 0},
                "false_set": {"water_radius": 99},
                "probe_values": [0, 1, 2, 3, 4, 6],
                "achiever": "approach_water",
                "cost": 2.0,
            },
            {
                "target": "time_of_day",
                "property": "time",
                "kind": "num",
                "var": "time_of_day",
                "comparator": ">=",
                "true_set": {"time_of_day": 18000},
                "false_set": {"time_of_day": 6000},
                "probe_values": [6000, 12000, 13000, 18000],
                "achiever": "set_time",
                "cost": 1.0,
            },
            {
                "target": "y_level",
                "property": "y",
                "kind": "num",
                "var": "y_level",
                "comparator": "<=",
                "true_set": {"y_level": -12},
                "false_set": {"y_level": 64},
                "probe_values": [64, 0, -5, -7, -8, -10, -12],
                "achiever": "descend",
                "cost": 2.5,
            },
        ]

    def _set_num(self, var: str, ga: GroundAction) -> StepResult:
        value = ga.binding("target")
        if value is None and ga.sets:
            value = ga.sets[0][1]
        if value is None:
            return StepResult(False, f"{ga.name} missing target binding")
        self.world.nums[var] = float(value)
        return StepResult(True)

    def _world_preconditions_hold(self, action_name: str) -> bool:
        spec = RECIPES.get(action_name)
        if spec is None:
            return False
        if any(self.world.inventory.get(item, 0) <= 0 for item in spec.get("inputs", {})):
            return False
        if self.condition == "drift" and action_name == self.task:
            gate = WORLD_GATES.get(action_name)
            if gate and not gate.holds(self.snapshot()):
                return False
        return True


def mcdrift_env_factory(task: str, condition: str, seed: int) -> MCDriftDownstreamEnv:
    return MCDriftDownstreamEnv(task, condition, seed)


def real_tasks():
    return [
        ("R1", "craftFence"),
        ("R2", "gatherCoalOre"),
        ("R3", "mineGoldOre"),
        ("C1", "craftBoat"),
        ("C2", "smeltRawIron"),
        ("C3", "mineDiamondOre"),
    ]
