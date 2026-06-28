"""Datapack registry for MC-Drift Phase 0-2.

Phase 1 generates recipe overrides for resource_update tasks.
Phase 2 adds a tag override for U16 / MineCoal requiring a stone-tier tool.

Important implementation note:
- In Minecraft 1.19 Java, datapack pack_format 10 is appropriate for 1.19-1.19.2.
  Override --pack-format if your exact minor version differs.
- Several resource_update tasks match vanilla material counts. We still emit
  explicit recipe overrides so the experimental manifest is reproducible.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _item(name: str) -> Dict[str, str]:
    return {"item": f"minecraft:{name}"}


RESOURCE_RECIPE_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "U00": {
        "recipe_id": "oak_fence",
        "note": "Actual drift: raises oak_planks requirement from vanilla 4 to 8.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["PPP", "PSP", "PPP"],
            "key": {"P": _item("oak_planks"), "S": _item("stick")},
            "result": {"item": "minecraft:oak_fence", "count": 3},
        },
    },
    "U02": {
        "recipe_id": "wooden_axe",
        "note": "Vanilla-equivalent control for stick >= 2; explicit override for reproducibility.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["PP", "PS", " S"],
            "key": {"P": _item("oak_planks"), "S": _item("stick")},
            "result": {"item": "minecraft:wooden_axe"},
        },
    },
    "U03": {
        "recipe_id": "wooden_sword",
        "note": "Vanilla-equivalent control for oak_planks >= 2.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["P", "P", "S"],
            "key": {"P": _item("oak_planks"), "S": _item("stick")},
            "result": {"item": "minecraft:wooden_sword"},
        },
    },
    "U05": {
        "recipe_id": "chest",
        "note": "Vanilla-equivalent control for oak_planks >= 8.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["PPP", "P P", "PPP"],
            "key": {"P": _item("oak_planks")},
            "result": {"item": "minecraft:chest"},
        },
    },
    "U06": {
        "recipe_id": "oak_door",
        "note": "Vanilla-equivalent control for oak_planks >= 6.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["PP", "PP", "PP"],
            "key": {"P": _item("oak_planks")},
            "result": {"item": "minecraft:oak_door", "count": 3},
        },
    },
    "U07": {
        "recipe_id": "ladder",
        "note": "Vanilla-equivalent control for stick >= 7.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["S S", "SSS", "S S"],
            "key": {"S": _item("stick")},
            "result": {"item": "minecraft:ladder", "count": 3},
        },
    },
    "U08": {
        "recipe_id": "oak_sign",
        "note": "Vanilla-equivalent control for stick >= 1.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["PPP", "PPP", " S "],
            "key": {"P": _item("oak_planks"), "S": _item("stick")},
            "result": {"item": "minecraft:oak_sign", "count": 3},
        },
    },
    "U11": {
        "recipe_id": "stone_sword",
        "note": "Explicit cobblestone recipe; vanilla may accept related stone-tool materials.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["C", "C", "S"],
            "key": {"C": _item("cobblestone"), "S": _item("stick")},
            "result": {"item": "minecraft:stone_sword"},
        },
    },
    "U22": {
        "recipe_id": "bucket",
        "note": "Vanilla-equivalent control for iron_ingot >= 3.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["I I", " I "],
            "key": {"I": _item("iron_ingot")},
            "result": {"item": "minecraft:bucket"},
        },
    },
    "U24": {
        "recipe_id": "iron_boots",
        "note": "Vanilla-equivalent control for iron_ingot >= 4.",
        "json": {
            "type": "minecraft:crafting_shaped",
            "pattern": ["I I", "I I"],
            "key": {"I": _item("iron_ingot")},
            "result": {"item": "minecraft:iron_boots"},
        },
    },
}


CAPABILITY_TAG_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "U16": {
        "tag_path": "data/minecraft/tags/blocks/needs_stone_tool.json",
        "note": "Coal ore normally can be harvested by lower-tier pickaxes; adding needs_stone_tool enforces held_tool(tier) >= stone.",
        "json": {
            "replace": False,
            "values": [
                "minecraft:coal_ore",
                "minecraft:deepslate_coal_ore",
            ],
        },
    }
}


def recipe_override_for(task_id: str) -> Optional[Dict[str, Any]]:
    return RESOURCE_RECIPE_OVERRIDES.get(task_id)


def capability_tag_override_for(task_id: str) -> Optional[Dict[str, Any]]:
    return CAPABILITY_TAG_OVERRIDES.get(task_id)
