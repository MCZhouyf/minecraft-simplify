"""Stage-1 integration acceptance (FIXED FLOW): tag-based capability biases C1-C3.

R1-R3 moved to the Fabric mod (craft_result + inventory_min) — see
tests/integration/test_bias_mod.py — because mineflayer's bot.craft() uses the
STATIC vanilla recipe list from minecraft-data and cannot arrange datapack-
modified recipe shapes (the positive case would be unsolvable, violating INV-1).

PRECONDITION (do this with the world CLOSED, then open it):
    python -m mc_drift.datapack_gen --biases all --install
Packs auto-enable at world load; each test then toggles so that ONLY the bias
under test is enabled, via /datapack enable|disable (safe internal reload — we
never touch the datapacks folder of an open world, avoiding the vanilla
'zip file closed' /reload bug).
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from tests.conftest import reset_with, run_chat, run_action, count_of  # noqa: E402

pytestmark = pytest.mark.integration

ORE_SPOT = (0, -40, 0)          # ADJUST: loaded chunk in your arena
ALL_PACK_IDS = ("C1", "C2", "C3")


@pytest.fixture()
def pack(request, env):
    """Enable ONLY the pack under test; leave all disabled afterwards."""
    ids = request.param if isinstance(request.param, list) else [request.param]
    env.datapacks_enable_only(ids, all_ids=ALL_PACK_IDS)
    try:
        yield ids
    finally:
        env.datapacks_enable_only([], all_ids=ALL_PACK_IDS)


def _mine_case(env, action, ore_block, pickaxe, out_item, expect_success):
    reset_with(env, {pickaxe: 1}, ORE_SPOT)
    run_chat(env,
             f"/fill {ORE_SPOT[0]-1} {ORE_SPOT[1]} {ORE_SPOT[2]-1} {ORE_SPOT[0]+3} {ORE_SPOT[1]+2} {ORE_SPOT[2]+1} minecraft:air",
             f"/fill {ORE_SPOT[0]-1} {ORE_SPOT[1]-1} {ORE_SPOT[2]-1} {ORE_SPOT[0]+3} {ORE_SPOT[1]-1} {ORE_SPOT[2]+1} minecraft:stone",
             f"/setblock {ORE_SPOT[0]+2} {ORE_SPOT[1]} {ORE_SPOT[2]} minecraft:{ore_block}")
    obs = run_action(env, action)
    n = count_of(obs, out_item)
    assert (n >= 1) == expect_success, (
        f"{action} with {pickaxe}: expected success={expect_success}, {out_item}={n}")


@pytest.mark.parametrize("pack", ["C1"], indirect=True)
def test_C1_deepslate_iron_needs_iron_pick(env, pack):
    _mine_case(env, "mineIronOre", "deepslate_iron_ore", "stone_pickaxe",
               "raw_iron", expect_success=False)
    _mine_case(env, "mineIronOre", "deepslate_iron_ore", "iron_pickaxe",
               "raw_iron", expect_success=True)


@pytest.mark.parametrize("pack", ["C2"], indirect=True)
def test_C2_gold_needs_diamond_pick(env, pack):
    _mine_case(env, "mineGoldOre", "gold_ore", "iron_pickaxe", "raw_gold", expect_success=False)
    _mine_case(env, "mineGoldOre", "gold_ore", "diamond_pickaxe", "raw_gold", expect_success=True)


@pytest.mark.parametrize("pack", ["C3"], indirect=True)
def test_C3_coal_needs_stone_pick(env, pack):
    _mine_case(env, "gatherCoalOre", "coal_ore", "wooden_pickaxe", "coal", expect_success=False)
    _mine_case(env, "gatherCoalOre", "coal_ore", "stone_pickaxe", "coal", expect_success=True)


def test_all_packs_disabled_restores_vanilla(env):
    """With every mc_drift pack disabled, vanilla tiers apply (gold: iron pick OK)."""
    env.datapacks_enable_only([], all_ids=ALL_PACK_IDS)
    _mine_case(env, "mineGoldOre", "gold_ore", "iron_pickaxe", "raw_gold", expect_success=True)
