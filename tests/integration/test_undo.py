"""Stage-6 integration: undo restores the verification context Ct on the live
bot (equip swap + chest stash), and context drift is actually detected."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import compiler as C                          # noqa: E402
from Adam.tcpg.executor import run_plan                      # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                    # noqa: E402
from tests.conftest import reset_with                        # noqa: E402

pytestmark = pytest.mark.integration

SURFACE_SPOT = (0, 70, 0)     # ADJUST: open ground


@pytest.fixture()
def rt(env):
    reset_with(env, {"diamond_pickaxe": 1, "iron_pickaxe": 1,
                     "coal": 3, "chest": 1}, SURFACE_SPOT)
    ok, _ = run_plan(env, [C.call("equip", name="diamond_pickaxe")])
    assert ok
    return TcpgRuntime(env, mode="tcpg", execute_action=lambda a: True)


def test_equip_swap_undo_restores_context(rt, env):
    snap = rt._snapshot()
    assert snap.get("held.name") == "diamond_pickaxe"
    ok, _ = run_plan(env, [C.call("equip", name="iron_pickaxe")])     # I-
    assert ok and not rt._ctx_matches(snap)                           # drift visible
    ok, _ = run_plan(env, [C.call("equip", name="diamond_pickaxe")])  # undo
    assert ok and rt._ctx_matches(snap)                               # restored


def test_chest_stash_undo_restores_items(rt, env):
    from Adam.tcpg.predicates import state_snapshot
    before = state_snapshot(env)["inventory"].get("coal", 0)
    assert before == 3
    deposit = [C.call("useChest", op="deposit", items=[{"name": "coal", "count": 3}])]
    withdraw = [C.call("useChest", op="withdraw", items=[{"name": "coal", "count": 3}])]
    ok, _ = run_plan(env, deposit)
    assert ok and state_snapshot(env)["inventory"].get("coal", 0) == 0
    ok, _ = run_plan(env, withdraw)
    assert ok and state_snapshot(env)["inventory"].get("coal", 0) == 3
