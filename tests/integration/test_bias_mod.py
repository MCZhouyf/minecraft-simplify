"""Stage 1+2 integration acceptance: the NINE engine event gates (Fabric mod mcdrift).\n\nR1-R3 (resource biases) live here since the stage-1 fix: mineflayer cannot\ncraft datapack-modified recipe shapes, so they are implemented as craft_result\ngates with require=inventory_min (vanilla shape unchanged -> INV-1 solvable).

Prereqs ON TOP OF stage 1 (see tests/conftest.py):
  * mcdrift jar built (`gradle build` in mc_drift/fabric-mod) and copied into
    <minecraft_dir>/mods/, game restarted once so the mod is loaded
  * world opened to LAN with CHEATS ON, IAP_MC_PORT exported
  * mc_drift/config.yaml filled (minecraft_dir / world_name)

Each test writes config/mcdrift.json with ONLY the gate under test (the mod
hot-reloads on file mtime; we also send /mcdrift reload to force it), runs the
negative then positive case, and finally disables all gates. Assertions are on
INVENTORY DELTAS only — INV-2 "no output" semantics.

Coordinates below are arena placeholders — adjust to spots in YOUR test world:
  SURFACE_SPOT : open-sky ground position
  SHALLOW_SPOT : underground, roof above, y > -32   (X1 negative / E2 negative)
  DEEP_SPOT    : underground, y <= -40              (X1 positive)
"""
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from mc_drift import datapack_gen as dg                       # noqa: E402
from tests.conftest import (reset_with, run_chat, run_action,   # noqa: E402
                            count_of)

pytestmark = pytest.mark.integration

SURFACE_SPOT = (0, 70, 0)     # ADJUST: open sky
SHALLOW_SPOT = (0, -20, 0)    # ADJUST: covered, y > -32
DEEP_SPOT = (0, -40, 0)       # ADJUST: y <= -40


def _mod_config_path():
    cfg = dg.load_config()
    return Path(cfg["minecraft_dir"]).expanduser() / "config" / "mcdrift.json"


def _clear_local_furnaces(env):
    run_chat(env, "/fill ~-4 ~-2 ~-4 ~4 ~4 ~4 minecraft:air replace minecraft:furnace")


@pytest.fixture()
def gate(request, env):
    """Enable exactly one mod gate; always disable all gates afterwards."""
    ids = request.param if isinstance(request.param, list) else [request.param]
    dg.export_mod_config(ids, _mod_config_path())
    run_chat(env, "/mcdrift reload")          # mtime hot-reload is the fallback
    time.sleep(2.5)
    try:
        yield ids
    finally:
        dg.export_mod_config([], _mod_config_path())
        run_chat(env, "/mcdrift reload")
        time.sleep(2.5)


# ------------------------------------------------------------------ R1: furnace needs coal on hand
@pytest.mark.parametrize("gate", ["R1"], indirect=True)
def test_R1_furnace_requires_coal(env, gate):
    inv = {"cobblestone": 8, "crafting_table": 1}
    reset_with(env, inv, SURFACE_SPOT)
    obs = run_action(env, "craftFurnace")
    assert count_of(obs, "furnace") == 0, "without coal in inventory the result must be withheld"

    reset_with(env, {**inv, "coal": 1}, SURFACE_SPOT)
    obs = run_action(env, "craftFurnace")
    assert count_of(obs, "furnace") >= 1, "with coal on hand crafting should succeed"
    assert count_of(obs, "coal") >= 1, "coal is a catalyst condition, not consumed"


# ------------------------------------------------------------------ R2: iron pickaxe needs flint on hand
@pytest.mark.parametrize("gate", ["R2"], indirect=True)
def test_R2_iron_pickaxe_requires_flint(env, gate):
    inv = {"iron_ingot": 3, "stick": 2, "crafting_table": 1}
    reset_with(env, inv, SURFACE_SPOT)
    obs = run_action(env, "craftIronPickaxe")
    assert count_of(obs, "iron_pickaxe") == 0, "without flint the result must be withheld"

    reset_with(env, {**inv, "flint": 1}, SURFACE_SPOT)
    obs = run_action(env, "craftIronPickaxe")
    assert count_of(obs, "iron_pickaxe") >= 1, "with flint on hand crafting should succeed"


# ------------------------------------------------------------------ R3: fence needs birch planks on hand
@pytest.mark.parametrize("gate", ["R3"], indirect=True)
def test_R3_fence_requires_birch_planks(env, gate):
    inv = {"oak_planks": 4, "stick": 2, "crafting_table": 1}
    reset_with(env, inv, SURFACE_SPOT)
    obs = run_action(env, "craftFence")
    assert count_of(obs, "oak_fence") == 0, "without birch planks the result must be withheld"

    reset_with(env, {**inv, "birch_planks": 4}, SURFACE_SPOT)
    obs = run_action(env, "craftFence")
    assert count_of(obs, "oak_fence") >= 1, "with birch planks on hand crafting should succeed"


# ------------------------------------------------------------------ P1: forge nearby
@pytest.mark.parametrize("gate", ["P1"], indirect=True)
def test_P1_iron_tools_need_furnace_nearby(env, gate):
    inv = {"iron_ingot": 3, "stick": 2, "crafting_table": 1}
    reset_with(env, inv, SURFACE_SPOT)                 # no furnace around
    _clear_local_furnaces(env)
    obs = run_action(env, "craftIronPickaxe")
    assert count_of(obs, "iron_pickaxe") == 0, "should be gated without a furnace nearby"

    reset_with(env, inv, SURFACE_SPOT)
    _clear_local_furnaces(env)
    run_chat(env, "/setblock ~2 ~ ~ minecraft:furnace")
    obs = run_action(env, "craftIronPickaxe")
    assert count_of(obs, "iron_pickaxe") >= 1, "furnace within r=3 should unlock crafting"


# ------------------------------------------------------------------ P2: furnace on stone
@pytest.mark.parametrize("gate", ["P2"], indirect=True)
def test_P2_raw_gold_needs_stone_base(env, gate):
    inv = {"raw_gold": 1, "coal": 2}
    reset_with(env, inv, SURFACE_SPOT)
    _clear_local_furnaces(env)
    run_chat(env, "/setblock ~2 ~-1 ~ minecraft:dirt", "/setblock ~2 ~ ~ minecraft:furnace")
    obs = run_action(env, "smeltRawGold")
    assert count_of(obs, "gold_ingot") == 0, "smelting raw_gold on dirt base must stall"

    reset_with(env, inv, SURFACE_SPOT)
    _clear_local_furnaces(env)
    run_chat(env, "/setblock ~2 ~-1 ~ minecraft:stone", "/setblock ~2 ~ ~ minecraft:furnace")
    obs = run_action(env, "smeltRawGold")
    assert count_of(obs, "gold_ingot") >= 1, "stone base should allow smelting"


# ------------------------------------------------------------------ X1: diamond depth gate
@pytest.mark.parametrize("gate", ["X1"], indirect=True)
def test_X1_diamond_only_below_y32(env, gate):
    reset_with(env, {"iron_pickaxe": 1}, SHALLOW_SPOT)
    run_chat(env,
             "/fill ~-1 ~ ~-1 ~3 ~2 ~1 minecraft:air",
             "/fill ~-1 ~-1 ~-1 ~3 ~-1 ~1 minecraft:stone",
             "/setblock ~2 ~ ~ minecraft:diamond_ore")
    obs = run_action(env, "mineDiamondOre")
    assert count_of(obs, "diamond") == 0, "above y=-32 the break must be cancelled"

    reset_with(env, {"iron_pickaxe": 1}, DEEP_SPOT)
    run_chat(env,
             "/fill ~-1 ~ ~-1 ~3 ~2 ~1 minecraft:air",
             "/fill ~-1 ~-1 ~-1 ~3 ~-1 ~1 minecraft:stone",
             "/setblock ~2 ~ ~ minecraft:diamond_ore")
    obs = run_action(env, "mineDiamondOre")
    assert count_of(obs, "diamond") >= 1, "below y=-32 mining should succeed"


# ------------------------------------------------------------------ X2: sand needs shovel
@pytest.mark.parametrize("gate", ["X2"], indirect=True)
def test_X2_sand_needs_held_shovel(env, gate):
    reset_with(env, {}, SURFACE_SPOT)                  # empty hand
    run_chat(env, "/setblock ~2 ~ ~ minecraft:sand")
    obs = run_action(env, "gatherSand")
    assert count_of(obs, "sand") == 0, "bare-handed sand digging must be gated"

    reset_with(env, {"wooden_shovel": 1}, SURFACE_SPOT)   # mineBlock auto-equips best tool
    run_chat(env, "/setblock ~2 ~ ~ minecraft:sand")
    obs = run_action(env, "gatherSand")
    assert count_of(obs, "sand") >= 1, "with a shovel held, sand should drop"


# ------------------------------------------------------------------ E1: daylight smelting
@pytest.mark.parametrize("gate", ["E1"], indirect=True)
def test_E1_smelting_only_by_day(env, gate):
    inv = {"raw_iron": 1, "coal": 2}
    reset_with(env, inv, SURFACE_SPOT)
    _clear_local_furnaces(env)
    run_chat(env, "/setblock ~2 ~ ~ minecraft:furnace", "/time set 18000")   # night
    obs = run_action(env, "smeltRawIron")
    assert count_of(obs, "iron_ingot") == 0, "furnace must stall at night"

    reset_with(env, inv, SURFACE_SPOT)
    _clear_local_furnaces(env)
    run_chat(env, "/setblock ~2 ~ ~ minecraft:furnace", "/time set 6000")    # day
    obs = run_action(env, "smeltRawIron")
    assert count_of(obs, "iron_ingot") >= 1, "daytime smelting should complete"


# ------------------------------------------------------------------ E2: open-sky crafting
@pytest.mark.parametrize("gate", ["E2"], indirect=True)
def test_E2_crafting_needs_open_sky(env, gate):
    reset_with(env, {"oak_log": 1}, SHALLOW_SPOT)      # covered -> no sky
    run_chat(env, "/setblock ~ ~2 ~ minecraft:stone")
    obs = run_action(env, "craftPlanks")
    assert count_of(obs, "oak_planks") == 0, "covered crafting must be gated"

    reset_with(env, {"oak_log": 1}, SURFACE_SPOT)
    run_chat(env, "/fill ~ ~1 ~ ~ ~6 ~ minecraft:air")
    obs = run_action(env, "craftPlanks")
    assert count_of(obs, "oak_planks") >= 1, "open-sky crafting should succeed"


# ------------------------------------------------------------------ gates-off sanity
def test_all_gates_disabled_restores_vanilla(env):
    dg.export_mod_config([], _mod_config_path())
    run_chat(env, "/mcdrift reload")
    time.sleep(2.5)
    reset_with(env, {"oak_log": 1}, SHALLOW_SPOT)
    obs = run_action(env, "craftPlanks")
    assert count_of(obs, "oak_planks") >= 1, "with gates disabled, covered crafting is vanilla"
