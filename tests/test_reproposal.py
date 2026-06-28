"""Offline tests for the closed-loop tail (Adam/tcpg/reproposal.py):
none-of-the-above detection, counterexample event, signature enumeration
fallback, and an end-to-end run where the LLM NEVER proposes the true cause
(a night-only gate) and the signature fallback recovers it. No Minecraft, no
API key (run_plan / eval_predicates / state_snapshot are mocked)."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import Adam.tcpg.runtime as RT                              # noqa: E402
import Adam.tcpg.reproposal as RP                           # noqa: E402
from Adam.tcpg.ccg import CCG                               # noqa: E402
from Adam.tcpg.proposer import Candidate                    # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                   # noqa: E402


# ============================================================= unit: NOTA
def _cand(target, value, status, action="mineGoldOre", comp=">=", prop="tier",
          dim="capability"):
    c = Candidate(action=action, dimension=dim, target=target, property=prop,
                  comparator=comp, value=value)
    c.status = status
    return c


def test_nota_fires_when_all_rejected_none_accepted():
    cands = {c.cid: c for c in [
        _cand("held_tool", "diamond", "rejected"),
        _cand("held_tool", "iron", "rejected"),
    ]}
    sig = RP.detect_none_of_the_above("mineGoldOre", cands, None)
    assert sig is not None
    assert sig["reason"] == "all_candidates_decided_none_accepted"
    assert len(sig["rejected_cids"]) == 2


def test_nota_silent_when_one_accepted():
    cands = {c.cid: c for c in [
        _cand("held_tool", "diamond", "accepted"),
        _cand("held_tool", "iron", "rejected"),
    ]}
    assert RP.detect_none_of_the_above("mineGoldOre", cands, None) is None


def test_nota_silent_when_still_undecided():
    cands = {c.cid: c for c in [
        _cand("held_tool", "diamond", "rejected"),
        _cand("y_level", -16, "undecided", comp="<=", prop="y", dim="context"),
    ]}
    assert RP.detect_none_of_the_above("mineGoldOre", cands, None) is None


def test_nota_fires_when_all_infeasible():
    cands = {c.cid: c for c in [
        _cand("nearby_block", "water", "observe_only", comp="<=k",
              prop="block", dim="context"),
    ]}
    sig = RP.detect_none_of_the_above("mineGoldOre", cands, None)
    assert sig is not None and sig["reason"] == "all_candidates_infeasible"


# ================================================= unit: signature fallback
def test_signature_fallback_covers_bounded_targets_only():
    obs = {"position": {"x": 1, "y": -20, "z": 3}, "block_below": "stone"}
    cands = RP.enumerate_signature_fallback("mineGoldOre", set(), obs)
    by_target = {}
    for c in cands:
        by_target.setdefault(c.target, []).append(c)
        ok, why = __import__("Adam.tcpg.proposer", fromlist=["validate"]).validate(c)
        assert ok, why                                       # every seed is legal
    # bounded-domain situational/capability targets are enumerated
    assert "held_tool" in by_target
    assert {str(c.value) for c in by_target["held_tool"]} >= {"iron", "diamond"}
    assert "time_of_day" in by_target
    vals = {tuple(c.value) for c in by_target["time_of_day"]}
    assert (12000, 24000) in vals and (0, 12000) in vals     # day + night
    assert "sky_exposed" in by_target
    assert "y_level" in by_target                            # seeded from obs y
    assert "block_below" in by_target                        # seeded from obs
    # open item/block-name domains are LEFT TO THE LLM (paper Sec. 7)
    assert "inventory_count" not in by_target
    assert "station_type" not in by_target


def test_signature_fallback_excludes_seen():
    obs = {"position": {"y": -20}}
    first = RP.enumerate_signature_fallback("mineGoldOre", set(), obs)
    exclude = {c.cid for c in first}
    again = RP.enumerate_signature_fallback("mineGoldOre", exclude, obs)
    assert again == []                                       # nothing new


def test_counterexample_event_mentions_rejected_and_asks_for_different():
    rej = [_cand("held_tool", "diamond", "rejected")]
    ev = RP.build_counterexample_event("mineGoldOre", "raw_gold",
                                       {"feedback": "no ore"}, rej)
    fb = ev["observation"]["feedback"]
    assert "REJECTED" in fb and "held_tool" in fb and "different" in fb.lower()
    assert "nearby_block" in fb and "block_below" in fb
    assert ev["action"] == "mineGoldOre"


def test_reproposal_with_none_llm_uses_proposer_default(monkeypatch):
    """Real runner passes llm=None for non-scripted runs; that must still call
    propose_from_failure's default LLM instead of skipping the LLM stage."""
    calls = []

    def fake_default_llm():
        def invoke(prompt):
            calls.append(prompt)
            return json.dumps([
                {"dimension": "context", "target": "nearby_block",
                 "property": "water", "comparator": "<=k", "value": 4},
            ])
        return invoke

    import Adam.tcpg.proposer as PR
    monkeypatch.setattr(PR, "_default_llm", fake_default_llm)

    out = RP.repropose(
        action="craftBoat",
        expected_effect="oak_boat",
        observation={"inventory": {"oak_planks": 5}},
        rejected=[_cand("inventory_count", 5, "rejected", action="craftBoat",
                        comp=">=", prop="oak_planks", dim="resource")],
        exclude_cids=set(),
        llm=None,
        use_signature_fallback=True,
    )

    assert calls, "counterexample LLM stage was skipped"
    assert any(c.target == "nearby_block" and c.property == "water" for c in out)


# ============================================ integration: night-gate recovery
class NightWorld:
    """mineGoldOre succeeds IFF it is night (time in [12000,24000)). The LLM
    never proposes time_of_day -- only a wrong depth confound -- so only the
    signature fallback can recover the true gate."""
    def __init__(self):
        self.inventory = {"iron_pickaxe": 1, "diamond_pickaxe": 1,
                          "crafting_table": 1, "dirt": 16}
        self.held = "iron_pickaxe"
        self.y = -20.0
        self.time = 6000           # day

    def execute_action(self, action):
        if action == "mineGoldOre":
            ok = 12000 <= (self.time % 24000) < 24000
            if ok:
                self.inventory["raw_gold"] = self.inventory.get("raw_gold", 0) + 1
            return ok
        return True

    def snapshot(self):
        return {"agent.y": self.y, "held.name": self.held,
                "held.tier": 4 if self.held == "diamond_pickaxe" else 3,
                "world.time_of_day": self.time, "world.is_raining": False,
                "block_below.name": "stone", "sky_exposed": False,
                "inventory": dict(self.inventory)}


class NightEnv:
    def __init__(self, world):
        self.world = world
        self.reset_calls = 0

    def reset(self, options=None):
        self.reset_calls += 1
        if options and "inventory" in options:
            self.world.inventory = dict(options["inventory"])
        return []


def _wrong_confound_llm(prompt):
    """Always proposes only a WRONG depth gate; never the true time gate."""
    return json.dumps([
        {"dimension": "context", "target": "y_level", "property": "y",
         "comparator": "<=", "value": -40},
    ])


@pytest.fixture()
def night_rig(monkeypatch):
    world = NightWorld()
    env = NightEnv(world)

    def fake_run_plan(env_, plan, cid="-", trial_id="-", step=-1):
        for call in plan:
            p, a = call["primitive"], call["args"]
            if p == "equip":
                world.held = a.get("name") or world.held
            elif p == "set_time":
                world.time = int(a["tick"]) % 24000
            elif p in ("set_y", "moveTo") and "y" in a:
                world.y = float(a["y"])
            elif p == "useChest":
                sgn = -1 if a["op"] == "deposit" else 1
                for it in a["items"]:
                    world.inventory[it["name"]] = max(
                        0, world.inventory.get(it["name"], 0) + sgn * it["count"])
            elif p in ("mineBlock", "craftItem", "smeltItem") and not a.get("special"):
                world.inventory[a["name"]] = world.inventory.get(a["name"], 0) \
                    + a.get("count", 1)
            elif p == "placeItem":
                world.inventory[a["name"]] = max(
                    0, world.inventory.get(a["name"], 0) - 1)
        return True, []

    import Adam.tcpg.executor as EX
    monkeypatch.setattr(EX, "run_plan", fake_run_plan)
    monkeypatch.setattr(RT, "run_plan", fake_run_plan, raising=False)

    import Adam.tcpg.predicates as P

    def fake_eval(env_, preds, timeout=60):
        s = world.snapshot()
        out = {}
        for p in preds:
            t = p["target"]
            if t == "held_tool":
                tiers = ["wooden", "golden", "stone", "iron", "diamond", "netherite"]
                cur = s["held.tier"]
                out[p["id"]] = {"id": p["id"], "known": True, "raw": world.held,
                                "value": int(cur >= tiers.index(p["value"])),
                                "error": None}
            elif t == "y_level":
                out[p["id"]] = {"id": p["id"], "known": True, "raw": s["agent.y"],
                                "value": int(s["agent.y"] <= float(p["value"])),
                                "error": None}
            elif t == "time_of_day":
                a, b = [int(x) for x in p["value"]]
                tod = int(s["world.time_of_day"]) % 24000
                out[p["id"]] = {"id": p["id"], "known": True, "raw": tod,
                                "value": int(a <= tod < b), "error": None}
            elif t == "sky_exposed":
                want = (p["value"] is True or str(p["value"]) == "true")
                out[p["id"]] = {"id": p["id"], "known": True, "raw": s["sky_exposed"],
                                "value": int(bool(s["sky_exposed"]) == want),
                                "error": None}
            else:
                out[p["id"]] = {"id": p["id"], "known": False, "raw": None,
                                "value": None, "error": "mock"}
        return out

    monkeypatch.setattr(P, "eval_predicates", fake_eval)
    monkeypatch.setattr(P, "state_snapshot", lambda env_, timeout=60: world.snapshot())
    return world, env


def test_closed_loop_recovers_llm_missed_night_gate(night_rig):
    """End-to-end: round-1 LLM proposes only a wrong depth gate -> intervention
    rejects it -> none-of-the-above -> signature fallback enumerates time_of_day
    -> the night gate is verified and accepted and written to the CCG. The
    discovery happens WITHOUT the LLM ever naming the true cause."""
    world, env = night_rig
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=world.execute_action, llm=_wrong_confound_llm,
                     config={"M": 20_000, "step_budget": 6000,
                             "max_interventions_per_event": 8, "n_min": 4,
                             "neighbor_expand": False,          # isolate the path
                             "nota_reproposal": True,
                             "max_reproposal_rounds": 2})

    accepted = None
    for _ in range(40):
        world.held = "iron_pickaxe"
        world.y = -20.0
        world.time = 6000                                   # day -> action fails
        rt.on_action("mineGoldOre", False, world.inventory,
                     {"inventory": dict(world.inventory)})
        accepted = next((c for c in rt.cands.values()
                         if c.target == "time_of_day"
                         and tuple(c.value) == (12000, 24000)
                         and c.status == "accepted"), None)
        if accepted is not None:
            break

    assert accepted is not None, "signature fallback failed to recover night gate"
    assert accepted.source == "signature_fallback"
    # the recovered gate is written back to the conditional causal graph
    assert any(c["target"] == "time_of_day" and c["status"] == "accepted"
               for c in rt.ccg.conditions.values())
    # the wrong depth confound the LLM proposed was rejected, never accepted
    assert all(not (c.target == "y_level" and c.status == "accepted")
               for c in rt.cands.values())
    # NOTA actually fired at least once
    assert rt._reproposal_rounds.get("mineGoldOre", 0) >= 1


def test_nota_reproposal_off_is_inert(night_rig):
    """With the flag OFF (default) the loop is byte-identical: no fallback, the
    night gate is NEVER discovered."""
    world, env = night_rig
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=world.execute_action, llm=_wrong_confound_llm,
                     config={"M": 20_000, "step_budget": 6000,
                             "max_interventions_per_event": 8, "n_min": 4,
                             "neighbor_expand": False})        # nota_reproposal off
    for _ in range(10):
        world.held = "iron_pickaxe"
        world.y = -20.0
        world.time = 6000
        rt.on_action("mineGoldOre", False, world.inventory,
                     {"inventory": dict(world.inventory)})
    assert not any(c.target == "time_of_day" for c in rt.cands.values())
    assert rt._reproposal_rounds == {}


def test_early_low_signal_nota_fires_after_rejected_zero_signal():
    rt = TcpgRuntime(None, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=lambda a: False, llm=lambda p: "[]",
                     config={"nota_reproposal": True})
    wrong = Candidate("craftFurnace", "resource", "inventory_count",
                      "cobblestone", ">=", 9)
    wrong.status = "rejected"
    undecided = Candidate("craftFurnace", "resource", "inventory_count",
                          "cobblestone", ">=", 10)
    for c in (wrong, undecided):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = RT.DualPool()
        rt.k5[c.cid] = {"feasible": True}

    sig = rt._early_low_signal_nota("craftFurnace")

    assert sig is not None
    assert sig["reason"] == "early_low_signal_none_accepted"
    assert sig["rejected_cids"] == [wrong.cid]


def test_early_low_signal_nota_waits_for_unexhausted_numeric_frontier():
    rt = TcpgRuntime(None, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=lambda a: False, llm=lambda p: "[]",
                     config={"nota_reproposal": True})
    c5 = Candidate("craftFence", "resource", "inventory_count",
                   "oak_planks", ">=", 5, source="frontier")
    c6 = Candidate("craftFence", "resource", "inventory_count",
                   "oak_planks", ">=", 6, source="frontier")
    c7 = Candidate("craftFence", "resource", "inventory_count",
                   "oak_planks", ">=", 7, source="frontier")
    c8 = Candidate("craftFence", "resource", "inventory_count",
                   "oak_planks", ">=", 8, source="frontier")
    for c in (c5, c6, c7, c8):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = RT.DualPool()
        rt.k5[c.cid] = {"feasible": True}
    for c in (c5, c6, c7):
        rt.pools[c.cid].n_pos = 1
        rt.pools[c.cid].n_neg = 1

    assert rt._early_low_signal_nota("craftFence") is None


def test_early_low_signal_nota_silent_when_boundary_has_positive_signal():
    rt = TcpgRuntime(None, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=lambda a: False, llm=lambda p: "[]",
                     config={"nota_reproposal": True})
    rejected = Candidate("craftFence", "resource", "inventory_count",
                         "oak_planks", ">=", 7)
    rejected.status = "rejected"
    boundary = Candidate("craftFence", "resource", "inventory_count",
                         "oak_planks", ">=", 8)
    for c in (rejected, boundary):
        rt.cands[c.cid] = c
        rt.pools[c.cid] = RT.DualPool()
        rt.k5[c.cid] = {"feasible": True}
    rt.pools[boundary.cid].n_pos = 1
    rt.pools[boundary.cid].k_pos = 1

    assert rt._early_low_signal_nota("craftFence") is None
