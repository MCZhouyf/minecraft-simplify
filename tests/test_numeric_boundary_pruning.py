import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

from Adam.tcpg.ccg import CCG  # noqa: E402
from Adam.tcpg.posterior import DualPool  # noqa: E402
from Adam.tcpg.proposer import Candidate  # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime  # noqa: E402


def _rt():
    return TcpgRuntime(None, ccg=CCG.init_default(), mode="tcpg",
                       execute_action=lambda a: False, llm=lambda p: "[]")


def _cand(value, status="undecided"):
    c = Candidate("craftFence", "resource", "inventory_count",
                  "oak_planks", ">=", value, source="frontier")
    c.status = status
    return c


def test_boundary_bridge_prioritizes_midpoint_between_reject_and_accept():
    rt = _rt()
    c7 = _cand(7, "rejected")
    c8 = _cand(8, "undecided")
    c9 = _cand(9, "accepted")
    c10 = _cand(10, "undecided")
    for c in (c7, c8, c9, c10):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
    assert rt._boundary_bridge("craftFence", [c.cid for c in (c8, c10)]) == c8.cid


def test_eligible_prunes_stronger_thresholds_after_minimum_accept():
    rt = _rt()
    c7 = _cand(7, "rejected")
    c8 = _cand(8, "undecided")
    c9 = _cand(9, "accepted")
    c10 = _cand(10, "undecided")
    c13 = _cand(13, "undecided")
    for c in (c7, c8, c9, c10, c13):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
    elig = rt._eligible("craftFence")
    assert c8.cid in elig
    assert c10.cid not in elig
    assert c13.cid not in elig


def test_eligible_prunes_stronger_thresholds_after_provisional_success():
    rt = _rt()
    c8 = _cand(8, "undecided")
    c9 = _cand(9, "undecided")
    c10 = _cand(10, "undecided")
    for c in (c8, c9, c10):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[c9.cid].n_pos = 1
    rt.pools[c9.cid].k_pos = 1
    elig = rt._eligible("craftFence")
    assert c8.cid in elig
    assert c9.cid in elig
    assert c10.cid not in elig


def test_promising_numeric_boundary_fills_positive_candidate_before_unrelated_group():
    rt = _rt()
    oak9 = _cand(9, "undecided")
    stick3 = Candidate("craftFence", "resource", "inventory_count",
                       "stick", ">=", 3, source="frontier")
    for c in (oak9, stick3):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[oak9.cid].n_pos = 2
    rt.pools[oak9.cid].k_pos = 2
    rt.pools[oak9.cid].n_neg = 2
    rt.pools[stick3.cid].n_pos = 3
    rt.pools[stick3.cid].n_neg = 3
    assert rt._promising_numeric_boundary("craftFence", [oak9.cid, stick3.cid]) == oak9.cid


def test_promising_numeric_boundary_continues_with_negative_noise():
    rt = _rt()
    oak9 = _cand(9, "undecided")
    for c in (oak9,):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[oak9.cid].n_pos = 4
    rt.pools[oak9.cid].k_pos = 4
    rt.pools[oak9.cid].n_neg = 2
    rt.pools[oak9.cid].k_neg = 1
    assert rt._promising_numeric_boundary("craftFence", [oak9.cid]) == oak9.cid


def test_promising_positive_non_numeric_candidate_is_resampled():
    rt = _rt()
    water = Candidate("craftBoat", "context", "nearby_block",
                      "water", "<=k", 4, source="tcpg")
    planks = Candidate("craftBoat", "resource", "inventory_count",
                       "oak_planks", ">=", 9, source="frontier")
    for c in (water, planks):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[water.cid].n_pos = 1
    rt.pools[water.cid].k_pos = 1
    rt.pools[water.cid].n_neg = 1
    rt.pools[water.cid].k_neg = 0

    assert rt._promising_positive_candidate(
        "craftBoat", [planks.cid, water.cid], {water.cid: 2.8, planks.cid: 2.0}
    ) == water.cid


def test_positive_reproposal_candidate_resampled_before_new_reproposal_probe(monkeypatch):
    rt = _rt()
    water = Candidate("craftBoat", "context", "nearby_block",
                      "water", "<=k", 4, source="tcpg")
    chest = Candidate("craftBoat", "resource", "inventory_count",
                      "chest", ">=", 1, source="tcpg")
    for c in (water, chest):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {
            "feasible": True,
            "plan_plus": [{"primitive": "moveToBlock",
                           "args": {"name": c.property}}],
            "plan_minus": [{"primitive": "moveTo",
                            "args": {"x": 0, "y": 70, "z": 0}}],
            "undo_plus": [{"primitive": "moveTo",
                           "args": {"x": 0, "y": 70, "z": 0}}],
            "undo_minus": [{"primitive": "moveTo",
                            "args": {"x": 0, "y": 70, "z": 0}}],
            "irreversible": False,
            "sim_verifiable": False,
        }
        rt.pools[c.cid] = DualPool()
        rt._reproposal_cids.add(c.cid)
    rt.pools[water.cid].n_pos = 1
    rt.pools[water.cid].k_pos = 1
    rt.pools[water.cid].n_neg = 1
    rt.pools[water.cid].k_neg = 0

    selected = []

    def fake_do(plan, cid, undo=False):
        if not undo:
            selected.append(cid)
        return True

    monkeypatch.setattr(rt, "_do", fake_do)
    monkeypatch.setattr(rt, "execute_action", lambda action: True)
    monkeypatch.setattr(rt, "_ctx_matches", lambda snap, cand: True)
    monkeypatch.setattr(rt, "_snapshot", lambda *a, **k: {"held.name": None, "agent.y": 70})
    rt.cfg["max_interventions_per_event"] = 1
    rt.cfg["step_budget"] = 10

    rt._verification_loop("craftBoat", {}, anchor=None)

    assert selected == [water.cid]


def test_numeric_overshoot_neighbor_pulls_adjacent_lower_threshold():
    rt = _rt()
    c8 = _cand(8, "undecided")
    c9 = _cand(9, "undecided")
    for c in (c8, c9):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[c9.cid].n_pos = 2
    rt.pools[c9.cid].k_pos = 2
    rt.pools[c9.cid].n_neg = 2
    rt.pools[c9.cid].k_neg = 1
    assert rt._numeric_overshoot_neighbor("craftFence", [c8.cid, c9.cid]) == c8.cid


def test_eligible_keeps_weaker_or_non_numeric_candidates():
    rt = _rt()
    c7 = _cand(7, "rejected")
    c8 = _cand(8, "undecided")
    c9 = _cand(9, "accepted")
    stick = Candidate("craftFence", "resource", "inventory_count",
                      "stick", ">=", 2, source="frontier")
    move = Candidate("craftFence", "context", "time_of_day",
                     "time", "=", [0, 12000], source="frontier")
    for c in (c7, c8, c9, stick, move):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
    elig = rt._eligible("craftFence")
    assert c8.cid in elig
    assert stick.cid in elig
    assert move.cid in elig


def _tool(value, status="undecided"):
    c = Candidate("gatherCoalOre", "capability", "held_tool",
                  "tier", ">=", value, source="neighbor")
    c.status = status
    return c


def test_eligible_prunes_stronger_tool_tiers_after_minimum_accept():
    rt = _rt()
    wooden = _tool("wooden", "undecided")
    stone = _tool("stone", "accepted")
    iron = _tool("iron", "undecided")
    for c in (wooden, stone, iron):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
    elig = rt._eligible("gatherCoalOre")
    assert wooden.cid in elig
    assert iron.cid not in elig


def test_ordered_overshoot_neighbor_pulls_adjacent_lower_tier():
    rt = _rt()
    stone = _tool("stone", "undecided")
    iron = _tool("iron", "undecided")
    for c in (stone, iron):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[iron.cid].n_pos = 2
    rt.pools[iron.cid].k_pos = 2
    rt.pools[iron.cid].n_neg = 0
    rt.pools[iron.cid].k_neg = 0
    assert rt._numeric_overshoot_neighbor("gatherCoalOre",
                                          [stone.cid, iron.cid]) == stone.cid


def test_promising_ordered_boundary_resamples_stone_before_unrelated():
    rt = _rt()
    stone = _tool("stone", "undecided")
    coal = Candidate("gatherCoalOre", "resource", "inventory_count",
                     "coal_ore", ">=", 3, source="frontier")
    for c in (stone, coal):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[stone.cid].n_pos = 1
    rt.pools[stone.cid].k_pos = 1
    rt.pools[stone.cid].n_neg = 1
    rt.pools[coal.cid].n_pos = 3
    rt.pools[coal.cid].n_neg = 3
    assert rt._promising_numeric_boundary("gatherCoalOre",
                                          [coal.cid, stone.cid]) == stone.cid


def test_min_floor_rank_prefers_active_ordered_boundary(monkeypatch):
    rt = _rt()
    stone = _tool("stone", "undecided")
    wooden = _tool("wooden", "undecided")
    for c in (stone, wooden):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True, "plan_plus": [], "plan_minus": [],
                        "undo_plus": [], "undo_minus": [], "irreversible": False}
        rt.pools[c.cid] = DualPool()
    rt.pools[stone.cid].n_pos = 1
    rt.pools[stone.cid].k_pos = 1
    rt.pools[stone.cid].n_neg = 1
    rt.pools[wooden.cid].n_pos = 1

    monkeypatch.setattr(rt, "_do", lambda *a, **k: False)
    monkeypatch.setattr(rt, "_costs", lambda: {stone.cid: 4.0, wooden.cid: 3.5})
    rt.cfg["min_verifications_per_cand"] = 2
    rt.cfg["max_interventions_per_event"] = 1
    rt._verification_loop("gatherCoalOre", {}, anchor=None)
    # The failed plan is logged against the selected candidate before abort.
    assert rt._plan_fail_counts.get(stone.cid) == 1
    assert rt._plan_fail_counts.get(wooden.cid, 0) == 0


def test_ordered_overshoot_stops_once_neighbor_has_positive_evidence():
    rt = _rt()
    wooden = _tool("wooden", "undecided")
    stone = _tool("stone", "undecided")
    iron = _tool("iron", "undecided")
    for c in (wooden, stone, iron):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt.pools[iron.cid].n_pos = 1
    rt.pools[iron.cid].k_pos = 1
    rt.pools[stone.cid].n_pos = 1
    rt.pools[stone.cid].k_pos = 1
    rt.pools[wooden.cid].n_pos = 1
    assert rt._numeric_overshoot_neighbor(
        "gatherCoalOre", [wooden.cid, stone.cid, iron.cid]) is None
    assert rt._promising_numeric_boundary(
        "gatherCoalOre", [wooden.cid, stone.cid, iron.cid]) == stone.cid


def test_active_ordered_boundary_prioritizes_natural_failure_neighbor(monkeypatch):
    rt = _rt()
    stone = _tool("stone", "undecided")
    coal = Candidate("gatherCoalOre", "resource", "inventory_count",
                     "coal_ore", ">=", 3, source="frontier")
    depth = Candidate("gatherCoalOre", "context", "y_level",
                      "y", "<=", 64, source="frontier")
    for c in (stone, coal, depth):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = DualPool()
    rt.pools[stone.cid].n_neg = 4
    rt.k5[stone.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "equip", "args": {"name": "stone_pickaxe"}}],
        "plan_minus": [{"primitive": "equip", "args": {"name": "wooden_pickaxe"}}],
        "undo_plus": [],
        "undo_minus": [],
        "irreversible": False,
        "sim_verifiable": True,
    }
    for c in (coal, depth):
        rt.k5[c.cid] = {
            "feasible": True,
            "plan_plus": [{"primitive": "set_count",
                           "args": {"name": c.property, "count": 3}}],
            "plan_minus": [{"primitive": "set_count",
                            "args": {"name": c.property, "count": 2}}],
            "undo_plus": [],
            "undo_minus": [],
            "irreversible": False,
            "sim_verifiable": True,
        }

    selected = []

    def fake_do(plan, cid, undo=False):
        if not undo:
            selected.append((cid, plan[0]["primitive"]))
        return True

    monkeypatch.setattr(rt, "_do", fake_do)
    monkeypatch.setattr(rt, "execute_action", lambda action: True)
    monkeypatch.setattr(rt, "_ctx_matches", lambda snap, cand: True)
    monkeypatch.setattr(rt, "_snapshot", lambda *a, **k: {"held.name": None, "agent.y": 70})
    rt.cfg["max_interventions_per_event"] = 1
    rt.cfg["step_budget"] = 10

    rt._verification_loop("gatherCoalOre", {}, anchor=None)

    assert selected == [(stone.cid, "equip")]


def test_failed_special_candidate_is_not_rescheduled():
    rt = _rt()
    furnace = Candidate("smeltRawIron", "context", "nearby_block",
                        "furnace", "<=", 3, source="tcpg")
    time = Candidate("smeltRawIron", "context", "time_of_day",
                     "time", "=", [13000, 23000], source="tcpg")
    for c in (furnace, time):
        rt.cands[c.cid] = c
        rt.k5[c.cid] = {"feasible": True}
        rt.pools[c.cid] = DualPool()
    rt._plan_fail_counts[furnace.cid] = 1

    sched = [cid for cid in rt._eligible("smeltRawIron") if rt._schedulable(cid)]
    assert furnace.cid not in sched
    assert time.cid in sched


def test_state_set_candidate_preempts_costly_situational_plan():
    rt = _rt()
    furnace = Candidate("smeltRawIron", "context", "nearby_block",
                        "furnace", "<=", 3, source="tcpg")
    time = Candidate("smeltRawIron", "context", "time_of_day",
                     "time", "=", [13000, 23000], source="tcpg")
    for c in (furnace, time):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = DualPool()
    rt.k5[furnace.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "mineBlock", "args": {"name": "oak_log"}}],
        "plan_minus": [],
    }
    rt.k5[time.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_time", "args": {"tick": 13000}}],
        "plan_minus": [{"primitive": "set_time", "args": {"tick": 6000}}],
    }

    selected = rt._state_set_candidate(
        "smeltRawIron", [furnace.cid, time.cid],
        {furnace.cid: 40.0, time.cid: 4.0},
    )
    assert selected == time.cid


def test_verification_loop_schedules_set_time_before_inventory_frontier(monkeypatch):
    rt = _rt()
    time = Candidate("smeltRawIron", "environment", "time_of_day",
                     "time", "in", [12000, 24000], source="frontier")
    coal = Candidate("smeltRawIron", "resource", "inventory_count",
                     "coal", ">=", 4, source="frontier")
    for c in (time, coal):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = DualPool()
    rt.k5[time.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_time", "args": {"tick": 18000}}],
        "plan_minus": [{"primitive": "set_time", "args": {"tick": 2000}}],
        "undo_plus": [{"primitive": "set_time", "args": {"tick": 6000}}],
        "undo_minus": [{"primitive": "set_time", "args": {"tick": 6000}}],
        "irreversible": False,
        "sim_verifiable": False,
    }
    rt.k5[coal.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_count", "args": {"name": "coal", "count": 4}}],
        "plan_minus": [{"primitive": "set_count", "args": {"name": "coal", "count": 3}}],
        "undo_plus": [{"primitive": "set_count", "args": {"name": "coal", "count": 3}}],
        "undo_minus": [{"primitive": "set_count", "args": {"name": "coal", "count": 3}}],
        "irreversible": False,
        "sim_verifiable": True,
    }

    selected = []

    def fake_do(plan, cid, undo=False):
        if not undo:
            selected.append((cid, plan[0]["primitive"]))
            rt._last_tick = plan[0].get("args", {}).get("tick")
        return True

    monkeypatch.setattr(rt, "_do", fake_do)
    monkeypatch.setattr(rt, "execute_action", lambda action: rt._last_tick == 18000)
    monkeypatch.setattr(rt, "_ctx_matches", lambda snap, cand: True)
    monkeypatch.setattr(rt, "_snapshot", lambda *a, **k: {"held.name": None, "agent.y": 70})
    rt.cfg["max_interventions_per_event"] = 1
    rt.cfg["step_budget"] = 10
    rt._verification_loop("smeltRawIron", {}, anchor=None)

    assert selected == [(time.cid, "set_time")]


def test_active_inventory_frontier_preempts_unrelated_y_level(monkeypatch):
    rt = _rt()
    planks = Candidate("craftFence", "resource", "inventory_count",
                       "oak_planks", ">=", 7, source="frontier")
    depth = Candidate("craftFence", "context", "y_level",
                      "y", "<=", 44, source="frontier")
    for c in (planks, depth):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = DualPool()
    rt.pools[planks.cid].n_neg = 3
    rt.pools[planks.cid].n_pos = 1
    rt.k5[planks.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_count", "args": {"name": "oak_planks", "count": 7}}],
        "plan_minus": [{"primitive": "set_count", "args": {"name": "oak_planks", "count": 6}}],
        "undo_plus": [{"primitive": "set_count", "args": {"name": "oak_planks", "count": 6}}],
        "undo_minus": [{"primitive": "set_count", "args": {"name": "oak_planks", "count": 6}}],
        "irreversible": False,
        "sim_verifiable": True,
    }
    rt.k5[depth.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_y", "args": {"y": 44}}],
        "plan_minus": [{"primitive": "set_y", "args": {"y": 70}}],
        "undo_plus": [{"primitive": "set_y", "args": {"y": 70}}],
        "undo_minus": [{"primitive": "set_y", "args": {"y": 70}}],
        "irreversible": False,
        "sim_verifiable": False,
    }

    selected = []

    def fake_do(plan, cid, undo=False):
        if not undo:
            selected.append((cid, plan[0]["primitive"]))
        return True

    monkeypatch.setattr(rt, "_do", fake_do)
    monkeypatch.setattr(rt, "execute_action", lambda action: False)
    monkeypatch.setattr(rt, "_ctx_matches", lambda snap, cand: True)
    monkeypatch.setattr(rt, "_snapshot", lambda *a, **k: {"held.name": None, "agent.y": 70})
    rt.cfg["max_interventions_per_event"] = 1
    rt.cfg["step_budget"] = 10
    rt._verification_loop("craftFence", {}, anchor=None)

    assert selected == [(planks.cid, "set_count")]


def test_positive_inventory_boundary_resampled_before_other_frontiers(monkeypatch):
    rt = _rt()
    oak8 = Candidate("craftFence", "resource", "inventory_count",
                     "oak_planks", ">=", 8, source="frontier")
    stick9 = Candidate("craftFence", "resource", "inventory_count",
                       "stick", ">=", 9, source="frontier")
    depth = Candidate("craftFence", "context", "y_level",
                      "y", "<=", 44, source="frontier")
    for c in (oak8, stick9, depth):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = DualPool()
    rt.pools[oak8.cid].n_pos = 3
    rt.pools[oak8.cid].k_pos = 3
    rt.pools[oak8.cid].n_neg = 3
    rt.pools[stick9.cid].n_pos = 3
    rt.pools[stick9.cid].n_neg = 3
    for c in (oak8, stick9):
        rt.k5[c.cid] = {
            "feasible": True,
            "plan_plus": [{"primitive": "set_count",
                           "args": {"name": c.property, "count": c.value}}],
            "plan_minus": [{"primitive": "set_count",
                            "args": {"name": c.property, "count": int(c.value) - 1}}],
            "undo_plus": [{"primitive": "set_count",
                           "args": {"name": c.property, "count": int(c.value) - 1}}],
            "undo_minus": [{"primitive": "set_count",
                            "args": {"name": c.property, "count": int(c.value) - 1}}],
            "irreversible": False,
            "sim_verifiable": True,
        }
    rt.k5[depth.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_y", "args": {"y": 44}}],
        "plan_minus": [{"primitive": "set_y", "args": {"y": 70}}],
        "undo_plus": [{"primitive": "set_y", "args": {"y": 70}}],
        "undo_minus": [{"primitive": "set_y", "args": {"y": 70}}],
        "irreversible": False,
        "sim_verifiable": False,
    }

    selected = []

    def fake_do(plan, cid, undo=False):
        if not undo:
            selected.append((cid, plan[0]["primitive"]))
        return True

    monkeypatch.setattr(rt, "_do", fake_do)
    monkeypatch.setattr(rt, "execute_action", lambda action: True)
    monkeypatch.setattr(rt, "_ctx_matches", lambda snap, cand: True)
    monkeypatch.setattr(rt, "_snapshot", lambda *a, **k: {"held.name": None, "agent.y": 70})
    rt.cfg["max_interventions_per_event"] = 1
    rt.cfg["step_budget"] = 10
    rt._verification_loop("craftFence", {}, anchor=None)

    assert selected == [(oak8.cid, "set_count")]


def test_verification_loop_returns_after_accepting_candidate(monkeypatch):
    rt = _rt()
    time = Candidate("smeltRawIron", "environment", "time_of_day",
                     "time", "in", [12000, 24000], source="frontier")
    coal = Candidate("smeltRawIron", "resource", "inventory_count",
                     "coal", ">=", 4, source="frontier")
    for c in (time, coal):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = DualPool()
    rt.k5[time.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_time", "args": {"tick": 18000}}],
        "plan_minus": [{"primitive": "set_time", "args": {"tick": 2000}}],
        "undo_plus": [{"primitive": "set_time", "args": {"tick": 6000}}],
        "undo_minus": [{"primitive": "set_time", "args": {"tick": 6000}}],
        "irreversible": False,
        "sim_verifiable": False,
    }
    rt.k5[coal.cid] = {
        "feasible": True,
        "plan_plus": [{"primitive": "set_count", "args": {"name": "coal", "count": 4}}],
        "plan_minus": [{"primitive": "set_count", "args": {"name": "coal", "count": 3}}],
        "undo_plus": [{"primitive": "set_count", "args": {"name": "coal", "count": 3}}],
        "undo_minus": [{"primitive": "set_count", "args": {"name": "coal", "count": 3}}],
        "irreversible": False,
        "sim_verifiable": True,
    }

    selected = []

    def fake_do(plan, cid, undo=False):
        if not undo:
            selected.append((cid, plan[0]["primitive"]))
            rt._last_tick = plan[0].get("args", {}).get("tick")
        return True

    monkeypatch.setattr(rt, "_do", fake_do)
    monkeypatch.setattr(rt, "execute_action", lambda action: rt._last_tick == 18000)
    monkeypatch.setattr(rt, "_ctx_matches", lambda snap, cand: True)
    monkeypatch.setattr(rt, "_snapshot", lambda *a, **k: {"held.name": None, "agent.y": 70})
    rt.cfg["max_interventions_per_event"] = 10
    rt.cfg["step_budget"] = 100
    rt.cfg["n_min"] = 1
    rt.cfg["tau_acc"] = 0.5

    rt._verification_loop("smeltRawIron", {}, anchor=None)

    assert time.status == "accepted"
    assert coal.status == "undecided"
    assert selected == [(time.cid, "set_time"), (time.cid, "set_time")]
