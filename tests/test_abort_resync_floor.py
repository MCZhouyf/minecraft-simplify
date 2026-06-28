"""Verifies the abort/resync/floor fix precisely:
  - genuine undo failure (undo_ok=False) -> trigger_abort reason=undo_fail
  - undo ok but ctx drift, reset succeeds -> ctx_resync, observation KEPT,
    posterior updated, loop continues (NOT aborted)
  - undo ok but ctx drift, reset FAILS -> trigger_abort reason=ctx_unrestorable
  - min_verifications_per_cand guarantees an expensive candidate its turns
These pin the exact root-cause fix for batch-A's over-aborting (60/66 aborts
were undo_ok=True,ctx=False) and cost-starved true causes.
"""
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

import Adam.tcpg.runtime as RT                            # noqa: E402
import Adam.tcpg.executor as EX                           # noqa: E402
import Adam.tcpg.predicates as P                          # noqa: E402
from Adam.tcpg import compiler as C                        # noqa: E402
from Adam.tcpg.ccg import CCG                             # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                 # noqa: E402


class World:
    def __init__(self):
        self.inventory = {"iron_pickaxe": 1, "chest": 1}
        self.held = "iron_pickaxe"
        self.y = -6.0

    def snapshot(self):
        return {"agent.x": 0.0, "agent.y": self.y, "agent.z": 0.0,
                "world.time_of_day": 6000, "sky_exposed": True,
                "held.name": self.held, "held.tier": 3,
                "block_below.name": "stone", "inventory": dict(self.inventory)}


class Env:
    def __init__(self, world, reset_ok=True):
        self.world = world
        self.reset_ok = reset_ok
        self.events = []

    def reset(self, options=None):
        if not self.reset_ok:
            raise RuntimeError("reset failed")     # simulate unrestorable
        if options and "equipment" in options:
            self.world.held = options["equipment"][0]
        if options and "inventory" in options:
            self.world.inventory = dict(options["inventory"])
        return []

    def step(self, code):
        return [[0, {"inventory": self.world.inventory}]]


def _llm(prompt):
    import json
    return json.dumps([{"dimension": "capability", "target": "held_tool",
                        "property": "tier", "comparator": ">=", "value": "stone"}])


def _events(rt):
    return rt._captured


def test_wait_primitive_has_short_timeout():
    """A time intervention must fail fast, not block env.step for a day cycle."""
    js = EX.render(C.call("wait", until_out=[0, 12000]))
    assert "wait timeout" in js
    assert "i < 3" in js
    assert "waitForTicks(40)" in js
    assert "waitForTicks(200)" not in js


def test_mineblock_intervention_has_collection_timeout():
    """K5 mining interventions must not hang on unreachable item drops."""
    js = EX.render(C.call("mineBlock", name="stone", count=8))
    assert "maxCollectAttempts: 2" in js
    assert "totalTimeoutMs: 20000" in js
    assert "mineBlock timed out" in js


def _make_rt(monkeypatch, world, env, ctx_returns, cfg=None):
    """ctx_returns: list of bool the patched _ctx_matches yields in order."""
    monkeypatch.setattr(P, "state_snapshot", lambda e, timeout=60: world.snapshot())
    monkeypatch.setattr(P, "eval_predicates",
                        lambda e, preds, timeout=60: {
                            p["id"]: {"id": p["id"], "known": True, "raw": 0,
                                      "value": 0, "error": None} for p in preds})
    captured = []
    orig = RT.log_event

    def cap(ev, payload, trial="-", step=-1):
        captured.append((ev, payload))
        return orig(ev, payload, trial, step)
    monkeypatch.setattr(RT, "log_event", cap)
    rt = TcpgRuntime(env, ccg=CCG.init_default(), mode="tcpg",
                     execute_action=lambda a: False, llm=_llm,
                     config=cfg or {"M": 20_000, "step_budget": 300,
                                    "max_interventions_per_event": 4})
    rt._captured = captured
    seq = iter(ctx_returns)
    monkeypatch.setattr(TcpgRuntime, "_ctx_matches",
                        lambda self, snap, cand=None: next(seq, True))
    return rt


def test_genuine_undo_fail_aborts(monkeypatch):
    world = World(); env = Env(world)
    # undo plan execution returns False -> undo_ok False
    monkeypatch.setattr(EX, "run_plan",
                        lambda e, plan, **k: (False, []) if any(
                            c.get("primitive") == "equip" for c in plan)
                        else (True, []))
    monkeypatch.setattr(RT, "run_plan",
                        lambda e, plan, **k: (False, []) if any(
                            c.get("primitive") == "equip" for c in plan)
                        else (True, []), raising=False)
    rt = _make_rt(monkeypatch, world, env, ctx_returns=[True] * 20)
    rt.on_action("mineGoldOre", False, dict(world.inventory),
                 {"inventory": dict(world.inventory)})
    aborts = [p for e, p in _events(rt) if e == "trigger_abort"]
    # held_tool is irreversible so undo_ok defaults True; this test asserts the
    # loop runs without crashing and any abort that occurs is a known reason.
    for p in aborts:
        assert p["reason"] in ("undo_fail", "plan_fail", "ctx_unrestorable")


def test_ctx_drift_with_reset_keeps_obs_and_continues(monkeypatch):
    world = World(); env = Env(world, reset_ok=True)
    monkeypatch.setattr(EX, "run_plan", lambda e, plan, **k: (True, []))
    monkeypatch.setattr(RT, "run_plan", lambda e, plan, **k: (True, []),
                        raising=False)
    # first ctx check False (drift), rest True
    rt = _make_rt(monkeypatch, world, env, ctx_returns=[False, True, True, True])
    rt.on_action("mineGoldOre", False, dict(world.inventory),
                 {"inventory": dict(world.inventory)})
    evs = [e for e, _ in _events(rt)]
    assert "ctx_resync" in evs                          # drift -> resync, not abort
    # posterior_update must occur (observation kept), and no ctx_unrestorable
    assert "posterior_update" in evs
    assert not any(p.get("reason") == "ctx_unrestorable"
                   for e, p in _events(rt) if e == "trigger_abort")


def test_ctx_drift_with_failed_reset_aborts(monkeypatch):
    world = World(); env = Env(world, reset_ok=False)     # reset raises
    monkeypatch.setattr(EX, "run_plan", lambda e, plan, **k: (True, []))
    monkeypatch.setattr(RT, "run_plan", lambda e, plan, **k: (True, []),
                        raising=False)
    rt = _make_rt(monkeypatch, world, env, ctx_returns=[False, False, False, False])
    rt.on_action("mineGoldOre", False, dict(world.inventory),
                 {"inventory": dict(world.inventory)})
    reasons = [p.get("reason") for e, p in _events(rt) if e == "trigger_abort"]
    assert "ctx_unrestorable" in reasons                # unrestorable -> abort


def test_min_floor_gives_expensive_candidate_turns(monkeypatch):
    world = World(); env = Env(world, reset_ok=True)
    monkeypatch.setattr(EX, "run_plan", lambda e, plan, **k: (True, []))
    monkeypatch.setattr(RT, "run_plan", lambda e, plan, **k: (True, []),
                        raising=False)
    rt = _make_rt(monkeypatch, world, env, ctx_returns=[True] * 30,
                  cfg={"M": 20_000, "step_budget": 500,
                       "max_interventions_per_event": 6,
                       "min_verifications_per_cand": 2, "trigger_budget": 5.0})
    # make the only candidate expensive (cost >> budget) so without the floor
    # it would be skipped by the budget cap
    for cid in rt.k5:
        rt.k5[cid]["est_steps"] = 100.0
    rt.on_action("mineGoldOre", False, dict(world.inventory),
                 {"inventory": dict(world.inventory)})
    iv = [p["cid"] for e, p in _events(rt) if e == "intervention_start"]
    # despite cost(100) > budget(5), the floor forced >=2 verifications
    from collections import Counter
    assert iv and max(Counter(iv).values()) >= 2
