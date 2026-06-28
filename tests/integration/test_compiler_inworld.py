"""Stage-5 integration acceptance: three intervention round trips on the live
bot via the executor (chest stash / equip swap / y-level move).

Prereqs: LAN world with cheats, IAP_MC_PORT exported (same as earlier stages).
No biases needed. These validate the do->retry->undo machinery; predicate
flips are checked through the stage-3 K3 API so the whole chain is exercised.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import compiler as C                        # noqa: E402
from Adam.tcpg.executor import run_plan                    # noqa: E402
from Adam.tcpg.predicates import eval_predicates           # noqa: E402
from Adam.tcpg.proposer import Candidate                   # noqa: E402
from tests.conftest import reset_with, count_of            # noqa: E402

pytestmark = pytest.mark.integration

DEEP_SPOT = (0, -40, 0)       # ADJUST: reachable underground spot
SURFACE_SPOT = (0, 70, 0)     # ADJUST: open ground


def _pred(target, prop, cmp_, val):
    return {"id": "p", "target": target, "property": prop,
            "comparator": cmp_, "value": val}


def _val(env, pred):
    r = eval_predicates(env, [pred])["p"]
    assert r["known"], f"predicate unknown: {r['error']}"
    return r["value"]


def test_chest_stash_round_trip(env):
    """inventory_count I- (deposit) then undo (withdraw): items never destroyed."""
    reset_with(env, {"coal": 3, "chest": 1}, SURFACE_SPOT)
    cand = Candidate("a", "resource", "inventory_count", "coal", ">=", 1)
    k5 = C.compile(cand, {"inventory": {"coal": 3, "chest": 1}})
    pred = _pred("inventory_count", "coal", ">=", 1)
    assert _val(env, pred) == 1

    ok, ev = run_plan(env, k5["plan_minus"])
    assert ok, f"I- failed: {ev[-1]}"
    assert _val(env, pred) == 0, "deposit should empty the coal slot"

    ok, ev = run_plan(env, k5["undo_minus"])
    assert ok, f"undo failed: {ev[-1]}"
    assert _val(env, pred) == 1, "withdraw should restore coal"


def test_equip_swap_round_trip(env):
    """held_tool I- (equip lower) then undo (re-equip original)."""
    reset_with(env, {"diamond_pickaxe": 1, "iron_pickaxe": 1}, SURFACE_SPOT)
    run_plan(env, [C.call("equip", name="diamond_pickaxe")])
    pred = _pred("held_tool", "tier", ">=", "diamond")
    assert _val(env, pred) == 1

    k5 = C.compile(Candidate("a", "capability", "held_tool", "tier", ">=", "diamond"),
                   {"inventory": {"diamond_pickaxe": 1, "iron_pickaxe": 1},
                    "held": "diamond_pickaxe"})
    ok, _ = run_plan(env, k5["plan_minus"])
    assert ok and _val(env, pred) == 0
    ok, _ = run_plan(env, k5["undo_minus"])
    assert ok and _val(env, pred) == 1


def test_y_level_move_round_trip(env):
    """y_level I+ (descend) then undo (return); pathfinder digs stairs."""
    reset_with(env, {"iron_pickaxe": 1}, DEEP_SPOT)
    start_y = DEEP_SPOT[1]
    k5 = C.compile(Candidate("a", "context", "y_level", "y", "<=", start_y - 4),
                   {"agent_y": start_y, "inventory": {"iron_pickaxe": 1}})
    pred = _pred("y_level", "y", "<=", start_y - 4)
    assert _val(env, pred) == 0

    ok, ev = run_plan(env, k5["plan_plus"])
    assert ok, f"descend failed: {ev[-1]}"
    assert _val(env, pred) == 1
    ok, _ = run_plan(env, k5["undo_plus"])
    assert ok and _val(env, pred) == 0
