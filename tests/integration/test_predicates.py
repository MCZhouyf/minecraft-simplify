"""Stage-3 integration acceptance: live predicate evaluation + state snapshot.

Prereqs: same as stage 1/2 (LAN world with cheats, IAP_MC_PORT exported).
No biases needed — disable all gates/packs or leave them, these tests only
read state. Adjust the two position constants to your arena.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.predicates import eval_predicates, state_snapshot   # noqa: E402
from tests.conftest import reset_with, run_chat                    # noqa: E402

pytestmark = pytest.mark.integration

SURFACE_SPOT = (0, 70, 0)     # ADJUST: open sky
DEEP_SPOT = (0, -40, 0)       # ADJUST: covered, y <= -40


def _equip(env, item):
    run_chat(env)  # no-op settle
    env.step(
        "const mcData = require('minecraft-data')(bot.version);\n"
        f"await bot.equip(mcData.itemsByName['{item}'].id, 'hand');\n"
        "await bot.waitForTicks(10);\n")


def P(id_, target, prop, cmp_, value):
    return {"id": id_, "target": target, "property": prop,
            "comparator": cmp_, "value": value}


def test_core_primitives_known_states(env):
    reset_with(env, {"iron_pickaxe": 1, "coal": 3}, DEEP_SPOT)
    _equip(env, "iron_pickaxe")
    run_chat(env, "/time set 6000", "/setblock ~2 ~ ~ minecraft:furnace",
             "/setblock ~2 ~-1 ~ minecraft:stone")
    res = eval_predicates(env, [
        P("c1", "inventory_count", "coal", ">=", 1),
        P("c2", "inventory_count", "coal", ">=", 5),
        P("t1", "held_tool", "tier", ">=", "stone"),
        P("t2", "held_tool", "tier", ">=", "diamond"),
        P("h1", "held_item", "type", "=", "pickaxe"),
        P("y1", "y_level", "y", "<=", -32),
        P("d1", "time_of_day", "time", "in", [0, 12000]),
        P("n1", "nearby_block", "furnace", "<=k", 3),
        P("s1", "station_base_block", "type", "=", "stone"),
        P("b1", "block_below", "type", "=", "bedrock"),
        P("w1", "weather", "state", "=", "clear"),
    ])
    expect = {"c1": 1, "c2": 0, "t1": 1, "t2": 0, "h1": 1,
              "y1": 1, "d1": 1, "n1": 1, "s1": 1, "w1": 1}
    for k, v in expect.items():
        assert res[k]["known"] is True, f"{k} unknown: {res[k]['error']}"
        assert res[k]["value"] == v, f"{k}: raw={res[k]['raw']}"
    assert res["b1"]["known"] is True            # value depends on arena floor

    run_chat(env, "/time set 18000")
    res2 = eval_predicates(env, [P("d2", "time_of_day", "time", "in", [0, 12000])])
    assert res2["d2"]["value"] == 0
    run_chat(env, "/time set 6000", "/setblock ~2 ~ ~ minecraft:air")
    res3 = eval_predicates(env, [P("n2", "nearby_block", "furnace", "<=k", 3)])
    assert res3["n2"]["value"] == 0 and res3["n2"]["known"] is True


def test_unknown_semantics(env):
    """known=False must be distinguishable from 0 (contract K3)."""
    reset_with(env, {}, SURFACE_SPOT)            # empty hand
    run_chat(env, "/fill ~-4 ~-2 ~-4 ~4 ~4 ~4 minecraft:air replace minecraft:furnace")
    res = eval_predicates(env, [
        P("u1", "held_item", "type", "=", "pickaxe"),       # empty hand -> unknown
        P("u2", "station_base_block", "type", "=", "stone"),  # no station -> unknown
        P("u3", "ingredient_type", "slot0", "=", "birch_planks"),  # action ctx -> unknown
        P("t0", "held_tool", "tier", ">=", "wooden"),       # empty hand tier=-1 -> KNOWN 0
    ])
    for k in ("u1", "u2", "u3"):
        assert res[k]["known"] is False and res[k]["value"] is None
    assert res["t0"]["known"] is True and res["t0"]["value"] == 0


def test_sky_exposed_both_sides(env):
    reset_with(env, {}, SURFACE_SPOT)
    r1 = eval_predicates(env, [P("k1", "sky_exposed", "sky", "=", True)])
    assert r1["k1"]["value"] == 1, f"surface should see sky: {r1['k1']}"
    reset_with(env, {}, DEEP_SPOT)
    r2 = eval_predicates(env, [P("k2", "sky_exposed", "sky", "=", True)])
    assert r2["k2"]["known"] is True and r2["k2"]["value"] == 0


def test_state_snapshot_fields(env):
    reset_with(env, {"coal": 2}, DEEP_SPOT)
    snap = state_snapshot(env)
    for field in ("agent.x", "agent.y", "agent.z", "world.time_of_day",
                  "world.is_raining", "held.name", "held.tier",
                  "block_below.name", "sky_exposed", "inventory"):
        assert field in snap, f"snapshot missing {field}"
    assert snap["inventory"].get("coal") == 2
    assert abs(snap["agent.y"] - DEEP_SPOT[1]) < 3
