"""Template-conditioned randomized drift generation for MC-Drift.

The generator emits K1-compatible bias entries plus origin/drift task pairs.
It is designed around the current IAP-Agent repository structure:

- mc_drift/biases/biases.yaml is the single source of truth for mechanisms.
- mc_drift/datapack_gen.py can generate datapacks or mod configs from a K1 file.
- mc_drift/solvability.py and Adam.tcpg.compiler verify oracle solvability and I+/I- compilability.

The templates below avoid free-form Minecraft rule generation. They only sample
mechanisms that can be represented by the current K1 schema and, for the default
Recoverable-Core split, by the current Adam.tcpg.compiler intervention macros.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence
import random
import re


ACTION_GOAL: Dict[str, str] = {
    "craftFurnace": "furnace",
    "craftIronPickaxe": "iron_pickaxe",
    "craftFence": "oak_fence",
    "craftPlanks": "oak_planks",
    "mineIronOre": "raw_iron",
    "mineGoldOre": "raw_gold",
    "gatherCoalOre": "coal",
    "mineDiamondOre": "diamond",
    "gatherSand": "sand",
    "smeltRawIron": "iron_ingot",
    "smeltRawGold": "gold_ingot",
}

RESULT_MATCH: Dict[str, str] = {
    "craftFurnace": "minecraft:furnace",
    "craftIronPickaxe": "minecraft:iron_pickaxe",
    "craftFence": "minecraft:.*_fence",
    "craftPlanks": "minecraft:.*_planks",
}

MINE_BLOCKS: Dict[str, List[str]] = {
    "mineIronOre": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
    "mineGoldOre": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore"],
    "gatherCoalOre": ["minecraft:coal_ore", "minecraft:deepslate_coal_ore"],
    "mineDiamondOre": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
    "gatherSand": ["minecraft:sand"],
}

TIER_TO_TAG = {
    "stone": "needs_stone_tool.json",
    "iron": "needs_iron_tool.json",
    "diamond": "needs_diamond_tool.json",
}
TIER_ORDER = ["wooden", "stone", "iron", "diamond"]

# Keep these within current compiler macros: nearby_block can only compile for
# PLACEABLE_STATIONS = {"furnace", "crafting_table", "chest"}.
PLACEABLE_CONTEXT_BLOCKS = ["furnace", "crafting_table", "chest"]


@dataclass(frozen=True)
class DriftSpec:
    """Internal representation of one unique drift specification."""

    family: str
    template: str
    action: str
    bias: Dict[str, Any]
    origin_task: str
    drift_task: str
    predicate_text: str
    distractors: List[Dict[str, Any]]


def _mc(name: str) -> str:
    return name if name.startswith("minecraft:") else f"minecraft:{name}"


def _feedback(dimension: str, hint: str) -> Dict[str, str]:
    return {"typed": f"[{dimension}]", "hinted": hint}


def _base_bias(
    *,
    action: str,
    dimension: str,
    level: str,
    mechanism: str,
    payload: Dict[str, Any],
    ground_truth: Dict[str, Any],
    hint: str,
) -> Dict[str, Any]:
    return {
        "id": "TMP",  # assigned after filtering/balanced selection
        "level": level,
        "dimension": dimension,
        "action": action,
        "mechanism": mechanism,
        "payload": payload,
        "ground_truth": ground_truth,
        "failure_mode": "no_output",
        "feedback_text": _feedback(dimension, hint),
        "authored_blind": False,
        "solvability": {"verified": None, "oracle_plan_steps": None},
        "intervention_check": {"i_plus_compilable": None, "i_minus_compilable": None},
    }


def _predicate_text(gt: Mapping[str, Any]) -> str:
    v = gt["value"]
    if isinstance(v, list):
        v = "[" + ", ".join(map(str, v)) + "]"
    return f"{gt['target']}({gt['property']}) {gt['comparator']} {v}"


def _task_pair(action: str, gt: Mapping[str, Any]) -> tuple[str, str]:
    goal = ACTION_GOAL.get(action, action)
    pred = _predicate_text(gt)
    origin = f"Obtain {goal} using the standard Minecraft mechanism for {action}."
    drift = f"Obtain {goal}; in the drifted environment, {action} succeeds only when {pred}."
    return origin, drift


def _distractors_for(gt: Mapping[str, Any], rng: random.Random) -> List[Dict[str, Any]]:
    """Generate structured but non-causal distractors for metadata/evaluation prompts."""
    target = gt["target"]
    prop = str(gt["property"])
    value = gt["value"]
    out: List[Dict[str, Any]] = []

    def add(target: str, prop: str, comparator: str, value: Any) -> None:
        cand = {"target": target, "property": prop, "comparator": comparator, "value": value}
        if cand != dict(gt) and cand not in out:
            out.append(cand)

    if target == "inventory_count":
        base = int(value)
        add(target, prop, ">=", max(1, base - 1))
        add(target, prop, ">=", base + 1)
        add(target, rng.choice(["oak_planks", "cobblestone", "coal", "birch_planks"]), ">=", base)
    elif target == "held_tool":
        choices = [t for t in TIER_ORDER if t != value]
        rng.shuffle(choices)
        for t in choices[:3]:
            add(target, prop, ">=", t)
    elif target == "y_level":
        base = int(value)
        add(target, prop, "<=", base + 4)
        add(target, prop, "<=", base - 4)
        add("time_of_day", "time", "in", [0, 12000])
    elif target == "time_of_day":
        add(target, prop, "in", [13000, 23000])
        add("sky_exposed", "sky", "=", True)
        add("y_level", "y", "<=", -10)
    elif target == "nearby_block":
        radius = int(value)
        add(target, prop, "<=k", max(1, radius - 1))
        add(target, rng.choice([b for b in PLACEABLE_CONTEXT_BLOCKS if b != prop]), "<=k", radius)
        add("inventory_count", "coal", ">=", 1)
    elif target == "station_base_block":
        add(target, prop, "=", "dirt")
        add("nearby_block", "furnace", "<=k", 3)
    elif target == "sky_exposed":
        add(target, prop, "=", not bool(value))
        add("time_of_day", "time", "in", [0, 12000])
    return out[:5]


# ------------------------------- sampling templates


def sample_resource_update(rng: random.Random) -> DriftSpec:
    action = rng.choice(["craftFurnace", "craftIronPickaxe", "craftFence", "craftPlanks"])
    item_pool = {
        "craftFurnace": [
            ("coal", 1), ("coal", 2),
            ("cobblestone", 8), ("cobblestone", 10),
            ("sand", 1), ("sand", 2),
        ],
        "craftIronPickaxe": [
            ("coal", 1), ("coal", 2),
            ("stick", 2), ("stick", 4),
            ("iron_ingot", 3), ("iron_ingot", 4),
            ("raw_iron", 1), ("raw_iron", 2),
        ],
        "craftFence": [
            ("birch_planks", 4), ("birch_planks", 8),
            ("oak_planks", 6), ("oak_planks", 8), ("oak_planks", 10),
            ("stick", 2), ("stick", 4),
        ],
        "craftPlanks": [
            ("oak_log", 1), ("oak_log", 2), ("oak_log", 3),
            ("birch_log", 1), ("birch_log", 2), ("birch_log", 3),
        ],
    }
    item, count = rng.choice(item_pool[action])
    gt = {"target": "inventory_count", "property": item, "comparator": ">=", "value": int(count)}
    payload = {
        "gate": "craft_result",
        "params": {
            "result_match": RESULT_MATCH[action],
            "require": "inventory_min",
            "item": _mc(item),
            "count": int(count),
        },
    }
    bias = _base_bias(
        action=action,
        dimension="resource",
        level="L1",
        mechanism="mod_event",
        payload=payload,
        ground_truth=gt,
        hint=f"The action now requires at least {count} {item} in inventory.",
    )
    origin, drift = _task_pair(action, gt)
    return DriftSpec("resource_update", "inventory_min", action, bias, origin, drift, _predicate_text(gt), _distractors_for(gt, rng))


def sample_capability_update(rng: random.Random) -> DriftSpec:
    tool_tier_choices = [
        ("gatherCoalOre", "stone", ["minecraft:coal_ore"]),
        ("mineIronOre", "iron", ["minecraft:iron_ore"]),
        ("mineGoldOre", "diamond", ["minecraft:deepslate_gold_ore"]),
    ]
    held_item_choices = [
        ("gatherSand", "minecraft:sand", "wooden_shovel"),
        ("gatherSand", "minecraft:sand", "wooden_pickaxe"),
        ("gatherSand", "minecraft:sand", "stone_pickaxe"),
        ("gatherSand", "minecraft:sand", "iron_pickaxe"),
        ("gatherSand", "minecraft:sand", "diamond_pickaxe"),
        ("gatherSand", "minecraft:sand", "stick"),
        ("gatherSand", "minecraft:sand", "chest"),
        ("gatherSand", "minecraft:sand", "crafting_table"),
        ("gatherSand", "minecraft:sand", "furnace"),
        ("gatherCoalOre", "minecraft:(deepslate_)?coal_ore", "wooden_pickaxe"),
        ("gatherCoalOre", "minecraft:(deepslate_)?coal_ore", "stone_pickaxe"),
        ("gatherCoalOre", "minecraft:(deepslate_)?coal_ore", "iron_pickaxe"),
        ("gatherCoalOre", "minecraft:(deepslate_)?coal_ore", "diamond_pickaxe"),
        ("gatherCoalOre", "minecraft:coal_ore", "wooden_pickaxe"),
        ("gatherCoalOre", "minecraft:coal_ore", "stone_pickaxe"),
        ("mineIronOre", "minecraft:(deepslate_)?iron_ore", "stone_pickaxe"),
        ("mineIronOre", "minecraft:(deepslate_)?iron_ore", "iron_pickaxe"),
        ("mineIronOre", "minecraft:(deepslate_)?iron_ore", "diamond_pickaxe"),
        ("mineIronOre", "minecraft:iron_ore", "stone_pickaxe"),
        ("mineIronOre", "minecraft:iron_ore", "iron_pickaxe"),
        ("mineGoldOre", "minecraft:(deepslate_)?gold_ore", "iron_pickaxe"),
        ("mineGoldOre", "minecraft:(deepslate_)?gold_ore", "diamond_pickaxe"),
        ("mineDiamondOre", "minecraft:(deepslate_)?diamond_ore", "iron_pickaxe"),
        ("mineDiamondOre", "minecraft:(deepslate_)?diamond_ore", "diamond_pickaxe"),
    ]
    if rng.random() < 0.2:
        action, tier, blocks = rng.choice(tool_tier_choices)
        gt = {"target": "held_tool", "property": "tier", "comparator": ">=", "value": tier}
        payload = {"tag_file": TIER_TO_TAG[tier], "values_add": blocks}
        bias = _base_bias(
            action=action,
            dimension="capability",
            level="L2",
            mechanism="datapack_tag",
            payload=payload,
            ground_truth=gt,
            hint=f"Mining now requires a {tier}-tier pickaxe or better.",
        )
        origin, drift = _task_pair(action, gt)
        return DriftSpec("capability_update", "tool_tier_tag", action, bias, origin, drift, _predicate_text(gt), _distractors_for(gt, rng))

    action, block_match, held_item = rng.choice(held_item_choices)
    gt = {"target": "held_item", "property": "type", "comparator": "=", "value": held_item}
    payload = {
        "gate": "block_break",
        "params": {
            "block_match": block_match,
            "require": "held_match",
            "value": _mc(held_item),
        },
    }
    bias = _base_bias(
        action=action,
        dimension="capability",
        level="L2",
        mechanism="mod_event",
        payload=payload,
        ground_truth=gt,
        hint=f"The action now requires holding {held_item}.",
    )
    origin, drift = _task_pair(action, gt)
    return DriftSpec("capability_update", "held_item_match", action, bias, origin, drift, _predicate_text(gt), _distractors_for(gt, rng))


def sample_boundary_update(rng: random.Random) -> DriftSpec:
    # Two compiler-supported boundary gates: y-level and time-of-day.
    if rng.random() < 0.55:
        action = rng.choice(["mineDiamondOre", "mineGoldOre"])
        block_match = {
            "mineDiamondOre": "minecraft:(deepslate_)?diamond_ore",
            "mineGoldOre": "minecraft:(deepslate_)?gold_ore",
        }[action]
        threshold = rng.choice([-4, -6, -8, -10, -12, -14, -16, -18, -20, -24, -32])
        gt = {"target": "y_level", "property": "y", "comparator": "<=", "value": threshold}
        payload = {"gate": "block_break", "params": {"block_match": block_match, "require": "player_y<=", "value": threshold}}
        bias = _base_bias(
            action=action,
            dimension="context",
            level="L4",
            mechanism="mod_event",
            payload=payload,
            ground_truth=gt,
            hint=f"The ore drops only at y <= {threshold}.",
        )
    else:
        action = rng.choice(["smeltRawIron", "smeltRawGold"])
        require = rng.choice(["daytime", "nighttime"])
        gt = {"target": "time_of_day", "property": "time", "comparator": "in", "value": [0, 12000] if require == "daytime" else [13000, 23000]}
        payload = {"gate": "furnace_tick", "params": {"require": require}}
        bias = _base_bias(
            action=action,
            dimension="environment",
            level="L5",
            mechanism="mod_event",
            payload=payload,
            ground_truth=gt,
            hint="The furnace only completes this recipe during daytime.",
        )
    origin, drift = _task_pair(action, gt)
    return DriftSpec("boundary_update", str(gt["target"]), action, bias, origin, drift, _predicate_text(gt), _distractors_for(gt, rng))


def sample_situational_discovery(rng: random.Random) -> DriftSpec:
    # Keep default split fully compilable: nearby_block must be a placeable station.
    if rng.random() < 0.7:
        action = rng.choice(["craftIronPickaxe", "craftFence", "craftPlanks", "smeltRawIron"])
        block = rng.choice(PLACEABLE_CONTEXT_BLOCKS)
        radius = rng.choice([2, 3, 4])
        gate = "craft_result" if action.startswith("craft") else "furnace_tick"
        params_key = "result_match" if gate == "craft_result" else "input_match"
        if gate == "craft_result":
            match_val = RESULT_MATCH.get(action, "minecraft:.*")
        else:
            match_val = "minecraft:raw_iron"
        params = {params_key: match_val, "require": "nearby_block", "block": _mc(block), "radius": radius}
        gt = {"target": "nearby_block", "property": block, "comparator": "<=k", "value": radius}
        payload = {"gate": gate, "params": params}
        bias = _base_bias(
            action=action,
            dimension="procedure",
            level="L3",
            mechanism="mod_event",
            payload=payload,
            ground_truth=gt,
            hint=f"The action now requires a nearby {block} within radius {radius}.",
        )
    else:
        action = rng.choice(["smeltRawIron", "smeltRawGold"])
        gt = {"target": "station_base_block", "property": "type", "comparator": "=", "value": "stone"}
        payload = {
            "gate": "furnace_tick",
            "params": {
                "input_match": "minecraft:raw_iron" if action == "smeltRawIron" else "minecraft:raw_gold",
                "require": "base_block_stone",
            },
        }
        bias = _base_bias(
            action=action,
            dimension="procedure",
            level="L3",
            mechanism="mod_event",
            payload=payload,
            ground_truth=gt,
            hint="The furnace must stand on a stone base.",
        )
    origin, drift = _task_pair(action, gt)
    return DriftSpec("situational_discovery", str(gt["target"]), action, bias, origin, drift, _predicate_text(gt), _distractors_for(gt, rng))


SAMPLERS: Dict[str, Callable[[random.Random], DriftSpec]] = {
    "resource_update": sample_resource_update,
    "capability_update": sample_capability_update,
    "boundary_update": sample_boundary_update,
    "situational_discovery": sample_situational_discovery,
}


def sample_spec(rng: random.Random, families: Sequence[str]) -> DriftSpec:
    family = rng.choice(list(families))
    return SAMPLERS[family](rng)


def spec_key(spec: DriftSpec) -> str:
    gt = spec.bias["ground_truth"]
    payload = spec.bias["payload"]
    return "|".join([
        spec.family,
        spec.template,
        spec.action,
        gt["target"],
        str(gt["property"]),
        str(gt["comparator"]),
        str(gt["value"]),
        str(payload),
    ])


def assign_k1_ids(specs: List[DriftSpec]) -> List[DriftSpec]:
    """Assign IDs that pass the repository's current K1 schema.

    Generated IDs use one schema-accepted letter plus a zero-padded number. The
    generated K1 file is intended as a standalone file, not an append to the
    hand-authored biases.yaml.
    """
    letters = list("RCPXEJF")
    per_letter = 1000
    if len(specs) > len(letters) * per_letter:
        raise ValueError("too many generated specs for current ID assignment space")
    out: List[DriftSpec] = []
    for i, spec in enumerate(specs):
        letter = letters[i // per_letter]
        digit = i % per_letter
        bias = dict(spec.bias)
        bias["id"] = f"{letter}{digit:03d}"
        out.append(DriftSpec(spec.family, spec.template, spec.action, bias, spec.origin_task, spec.drift_task, spec.predicate_text, spec.distractors))
    return out
