"""Offline tests for stage 5: intervention compiler (K5) + executor rendering."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import compiler as C                       # noqa: E402
from Adam.tcpg.executor import render                     # noqa: E402
from Adam.tcpg.proposer import Candidate                  # noqa: E402

K5_KEYS = {"candidate_id", "feasible", "est_steps", "plan_plus", "plan_minus",
           "undo_plus", "undo_minus", "irreversible", "infeasible_reason",
           "sim_verifiable"}


def cand(target, prop, cmp_, val, dim="context", action="a"):
    return Candidate(action, dim, target, prop, cmp_, val)


def prims(plan):
    return [c["primitive"] for c in plan]


# ----------------------------------------------------------- acquisition
def test_acquire_recursion_and_inventory_credit():
    steps, est = C.acquire_plan("iron_pickaxe", 1, {"iron_ingot": 3, "stick": 2,
                                                    "crafting_table": 1})
    names = prims(steps)
    assert names[-1] == "craftItem" and "mineBlock" not in names   # fully credited
    steps2, est2 = C.acquire_plan("iron_pickaxe", 1, {})
    assert est2 > est and "smeltItem" in prims(steps2)             # full chain
    assert any(c["args"].get("name") == "crafting_table"
               for c in steps2 if c["primitive"] == "placeItem")


def test_acquire_cycle_guard():
    import copy
    bad = copy.deepcopy(C.RECIPES)
    bad["diamond"]["tool_tier"] = 3                                # circular
    with pytest.raises(C.Infeasible) as e:
        C.acquire_plan("diamond_pickaxe", 1, {}, recipes=bad)
    assert e.value.reason == "recipe_unreachable"


# ----------------------------------------------------------- templates
def test_k5_shape_and_held_tool_template():
    k5 = C.compile(cand("held_tool", "tier", ">=", "diamond", "capability"),
                   {"inventory": {"iron_pickaxe": 1, "crafting_table": 1},
                    "held": "iron_pickaxe", "agent_y": -20})
    assert set(k5) == K5_KEYS and k5["feasible"]
    assert k5["plan_plus"][0] == C.call("set_count", name="diamond_pickaxe",
                                        count=1, special="exact")
    assert prims(k5["plan_plus"])[-1] == "equip"
    assert k5["plan_minus"] == [
        C.call("set_count", name="iron_pickaxe", count=1, special="exact"),
        C.call("equip", name="iron_pickaxe"),
    ]   # lower tier
    assert k5["undo_minus"][-1]["args"]["name"] == "iron_pickaxe"


def test_held_tool_stone_maps_to_stone_pickaxe():
    k5 = C.compile(cand("held_tool", "tier", ">=", "stone", "capability"),
                   {"inventory": {"wooden_pickaxe": 1},
                    "held": "wooden_pickaxe"})
    assert k5["feasible"]
    assert k5["plan_plus"][0] == C.call("set_count", name="stone_pickaxe",
                                        count=1, special="exact")
    assert k5["plan_plus"][1] == C.call("equip", name="stone_pickaxe")


def test_inventory_count_stash_discipline():
    k5 = C.compile(cand("inventory_count", "coal", ">=", 1, "resource"),
                   {"inventory": {"coal": 2, "chest": 1}})
    assert k5["plan_plus"] == []                                   # already true
    assert any(c["primitive"] == "useChest" and c["args"]["op"] == "deposit"
               for c in k5["plan_minus"])
    assert any(c["primitive"] == "useChest" and c["args"]["op"] == "withdraw"
               for c in k5["undo_minus"])
    assert not any(c["primitive"] == "mineBlock" and c["args"].get("name") == "coal"
                   for c in k5["plan_minus"])                      # never destroys


def test_y_level_moves_and_undo_returns():
    k5 = C.compile(cand("y_level", "y", "<=", -32), {"agent_y": 64})
    assert k5["plan_plus"] == [C.call("set_y", y=-34)]
    assert k5["plan_minus"] == [C.call("set_y", y=-26)]
    assert k5["undo_plus"] == [C.call("set_y", y=64)]


def test_time_irreversible_and_weather_infeasible():
    k5 = C.compile(cand("time_of_day", "time", "in", [0, 12000], "environment"))
    assert not k5["irreversible"]
    assert k5["plan_plus"] == [C.call("set_time", tick=6000)]
    assert k5["plan_minus"] == [C.call("set_time", tick=14000)]
    assert k5["undo_plus"] == [C.call("set_time", tick=6000)]
    k5w = C.compile(cand("weather", "state", "=", "clear", "environment"))
    assert not k5w["feasible"] and k5w["infeasible_reason"] == "not_intervenable"
    k5i = C.compile(cand("ingredient_type", "slot0", "=", "birch_planks", "resource"))
    assert k5i["infeasible_reason"] == "no_macro"


def test_time_night_window_sets_night_and_day_contrast():
    k5 = C.compile(cand("time_of_day", "time", "in", [12000, 24000], "environment"))
    assert k5["feasible"]
    assert k5["plan_plus"] == [C.call("set_time", tick=18000)]
    assert k5["plan_minus"] == [C.call("set_time", tick=2000)]


def test_nearby_block_placeable_vs_ore():
    ok = C.compile(cand("nearby_block", "furnace", "<=k", 3, "procedure"),
                   {"inventory": {"furnace": 1}})
    assert ok["feasible"] and prims(ok["plan_plus"]) == ["placeItem"]
    assert ok["plan_minus"] == [C.call("moveTo", dx=6)]
    water = C.compile(cand("nearby_block", "water", "<=k", 4, "context"))
    assert water["feasible"], water["infeasible_reason"]
    assert water["plan_plus"] == [
        C.call("moveToBlock", name="water", radius=4, maxDistance=32)
    ]
    assert water["plan_minus"] == [C.call("moveTo", dx=7)]
    bad = C.compile(cand("nearby_block", "gold_ore", "<=k", 8, "context"))
    assert not bad["feasible"] and bad["infeasible_reason"] == "no_macro"


def test_station_type_value_is_block_name_not_radius():
    """Regression: station_type candidates carry value=<block name>, so the
    compiler must NOT do int(value). The crashing case from the R-suite run
    (station_type=crafting_table) must compile to a place/move intervention."""
    k5 = C.compile(cand("station_type", "type", "=", "crafting_table", "procedure"),
                   {"inventory": {}})
    assert k5["feasible"], k5["infeasible_reason"]
    assert prims(k5["plan_plus"])[-1] == "placeItem"
    assert k5["plan_minus"][0]["primitive"] == "moveTo"
    have = C.compile(cand("station_type", "type", "=", "furnace", "procedure"),
                     {"inventory": {"furnace": 1}})
    assert have["feasible"] and prims(have["plan_plus"]) == ["placeItem"]
    nm = C.compile(cand("station_type", "type", "=", "anvil", "procedure"))
    assert nm["infeasible_reason"] == "no_macro"


def test_compile_never_raises_on_malformed_values():
    """K5 robustness contract: compile() returns no_macro for shape-mismatched
    candidates; it must not propagate ValueError into the runtime loop."""
    bad = C.compile(cand("nearby_block", "crafting_table", "<=k", "crafting_table",
                         "procedure"), {"inventory": {}})
    assert bad["infeasible_reason"] == "no_macro" and not bad["feasible"]


def test_station_base_and_sky_templates():
    k5 = C.compile(cand("station_base_block", "type", "=", "stone", "procedure"),
                   {"inventory": {"cobblestone": 1, "furnace": 1}})
    where = [c["args"].get("where") for c in k5["plan_plus"]]
    assert where == ["near", "on_last"]
    sky = C.compile(cand("sky_exposed", "sky", "=", True, "environment"))
    assert sky["plan_plus"][0]["args"].get("special") == "roof_column"
    assert sky["plan_minus"][0]["args"].get("where") == "roof"


def test_step_cap_and_monotone_cost():
    rich = C.compile(cand("held_tool", "tier", ">=", "diamond", "capability"),
                     {"inventory": {"diamond_pickaxe": 1}})
    poor = C.compile(cand("held_tool", "tier", ">=", "diamond", "capability"),
                     {"inventory": {}})
    assert rich["est_steps"] == poor["est_steps"]  # reset realization is inventory-independent
    capped = C.compile(cand("held_tool", "tier", ">=", "diamond", "capability"),
                       {"inventory": {}}, step_cap=1)
    assert capped["infeasible_reason"] == "exceeds_step_cap"


def test_dry_run_backfill_shape():
    dr = C.dry_run(cand("held_tool", "tier", ">=", "iron", "capability"))
    assert set(dr) == {"feasible", "est_steps", "infeasible_reason",
                       "i_plus_compilable", "i_minus_compilable"}


# ----------------------------------------------------------- executor rendering
def test_render_every_primitive_compiles_to_js():
    calls = [C.call("mineBlock", name="oak_log", count=2),
             C.call("mineBlock", name="_roof", special="roof_column", count=6),
             C.call("craftItem", name="furnace", count=1),
             C.call("smeltItem", name="iron_ingot", fuel="coal", count=1),
             C.call("placeItem", name="furnace", where="near"),
             C.call("placeItem", name="furnace", where="on_last"),
             C.call("placeItem", name="dirt", where="roof"),
             C.call("useChest", op="deposit", items=[{"name": "coal", "count": 2}]),
             C.call("useChest", op="withdraw", items=[{"name": "coal", "count": 2}]),
             C.call("equip", name="iron_pickaxe"),
             C.call("moveTo", y=-36), C.call("moveTo", dx=6),
             C.call("moveToBlock", name="water", radius=4, maxDistance=32),
             C.call("set_y", y=-36), C.call("set_time", tick=18000),
             C.call("wait", until_in=[0, 12000]),
             C.call("wait", until_out=[0, 12000])]
    for c in calls:
        js = render(c)
        assert "async function mineBlock" in js and "await " in js
    with pytest.raises(ValueError):
        render({"primitive": "teleport", "args": {}})
