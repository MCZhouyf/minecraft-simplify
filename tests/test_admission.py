"""Offline tests for stage 4 primitive admission (paper App. B.6, contract K9)."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import admission as A                     # noqa: E402
from Adam.tcpg import proposer as PR                     # noqa: E402

FIELDS = {"agent.x": "number", "agent.y": "number", "agent.z": "number",
          "block_below.name": "string", "held.name": "string",
          "held.tier": "number", "inventory": "map", "sky_exposed": "bool",
          "world.is_raining": "bool", "world.time_of_day": "number"}


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isolate persistence so tests never touch the repo files."""
    monkeypatch.setattr(A, "REJECTS_PATH", tmp_path / "rejects.json")
    monkeypatch.setattr(A, "RUNTIME_SCHEMA_PATH", tmp_path / "schema_runtime.json")
    monkeypatch.setattr(PR, "RUNTIME_SCHEMA_PATH", tmp_path / "schema_runtime.json")
    monkeypatch.setattr(A, "load_state_fields", lambda path=None: FIELDS)
    return tmp_path


# ----------------------------------------------------------------- Q grammar
@pytest.mark.parametrize("src", [
    "agent.y <= -32",
    "inventory[coal] >= 1 and agent.y <= -32",
    "not sky_exposed == true",
    "world.time_of_day >= 0 and world.time_of_day <= 12000",
    "block_below.name in {stone, deepslate, cobblestone}",
    "abs(agent.y - 10) <= 5",
    "count(inventory) >= 3",
    "(agent.y <= 0 or sky_exposed == false) and held.tier >= 2",
])
def test_q_parses_legal(src):
    ast = A.parse(src)
    ok, why = A.static_check(ast, FIELDS)
    assert ok, f"{src}: {why}"


@pytest.mark.parametrize("src,frag", [
    ("", "empty"),
    ("agent.y <=", "term"),
    ("while true", "comparator"),
    ("agent.y <= -32 and", "term"),
    ("import os", "comparator"),
    ("agent.y ** 2 >= 0", "illegal character"),
])
def test_q_rejects_illegal_syntax(src, frag):
    with pytest.raises(A.QSyntaxError) as e:
        A.parse(src)
    assert frag in str(e.value).lower()


@pytest.mark.parametrize("src,frag", [
    ("mana.level >= 3", "not in observation interface"),
    ("held.name >= 2", "numeric"),
    ("sky_exposed == 1", "literal type mismatch"),
    ("inventory == empty", "map field"),
    ("agent.y in {stone}", "string field"),
    ("agent.y + 1", "comparator"),                    # numeric root cannot parse as bexpr
])
def test_static_check_rejects(src, frag):
    try:
        ast = A.parse(src)
    except A.QSyntaxError as exc:
        assert frag in str(exc).lower()
        return
    ok, why = A.static_check(ast, FIELDS)
    assert not ok and frag in why.lower()


# ----------------------------------------------------------------- evaluation
def test_evaluate_semantics_and_unknown():
    ast = A.parse("inventory[coal] >= 2 and sky_exposed == false")
    assert A.evaluate(ast, {"inventory": {"coal": 3}, "sky_exposed": False}) == (True, True)
    assert A.evaluate(ast, {"inventory": {}, "sky_exposed": False}) == (False, True)
    # missing scalar -> unknown propagates...
    assert A.evaluate(ast, {"inventory": {"coal": 3}}) == (None, False)
    # ...but and short-circuits on a known False side
    assert A.evaluate(ast, {"inventory": {"coal": 0}}) == (False, True)


def test_dynamic_check_filters():
    good = A.parse("agent.y <= -32")
    snaps = [{"agent.y": float(y)} for y in (-40, -10, -50, 5)] * 15
    ok, stats = A.dynamic_check(good, snaps)
    assert ok and stats["distinct"] == 2
    const = A.parse("agent.y <= 1000")
    ok, stats = A.dynamic_check(const, snaps)
    assert not ok and "constant" in stats["reason"]
    mostly_unknown = A.parse("held.tier >= 2")
    ok, stats = A.dynamic_check(mostly_unknown, [{"agent.y": 1.0}] * 60)
    assert not ok and "unknown" in stats["reason"]


# ----------------------------------------------------------------- full pipeline
SNAPS = ([{"agent.y": -40.0, "world.time_of_day": 6000} ] * 30
         + [{"agent.y": 10.0, "world.time_of_day": 18000}] * 30)


def _llm_ok(prompt):
    return json.dumps({"property_name": "deep_layer",
                       "expr": "agent.y <= -32",
                       "gloss": "agent is in the deep layer"})


def test_try_admit_full_flow_and_validate_pass_through(iso):
    # below trigger -> refused
    rec, why = A.try_admit("y_zone", "depth", SNAPS, _llm_ok)
    assert rec is None and "trigger" in why
    for _ in range(3):
        A.record_rejection("y_zone", "depth")
    rec, why = A.try_admit("y_zone", "depth", SNAPS, _llm_ok)
    assert rec is not None and why == ""
    assert rec["intervenable"] is True and rec["macro_family"] == "y_level"
    # K9 persisted -> proposer.validate now accepts the admitted target
    c = PR.Candidate("mineDiamondOre", "context", "deep_layer", "expr", "=", True)
    ok, _ = PR.validate(c)
    assert ok
    ok, why = PR.validate(PR.Candidate("a", "context", "deep_layer", "expr", ">=", 1))
    assert not ok and "boolean" in why
    # duplicate admission refused
    rec2, why2 = A.try_admit("y_zone", "depth", SNAPS, _llm_ok)
    assert rec2 is None and "already admitted" in why2


def test_caps_and_snapshot_floor(iso):
    for _ in range(3):
        A.record_rejection("t", "p")
    rec, why = A.try_admit("t", "p", SNAPS[:10], _llm_ok)
    assert rec is None and "snapshots" in why
    rec, why = A.try_admit("t", "p", SNAPS, _llm_ok, episode_admitted=1)
    assert rec is None and "per-episode" in why
    doc = {"version": 1, "admitted": [{"property_name": f"x{i}"} for i in range(8)]}
    A.save_runtime(doc)
    rec, why = A.try_admit("t", "p", SNAPS, _llm_ok)
    assert rec is None and "global" in why


def test_bad_admission_replies_rejected(iso):
    for _ in range(3):
        A.record_rejection("t", "p")
    rec, why = A.try_admit("t", "p", SNAPS, lambda p: "not json")
    assert rec is None and "invalid" in why
    rec, why = A.try_admit(
        "t", "p", SNAPS,
        lambda p: json.dumps({"property_name": "bad", "expr": "mana.x >= 1",
                              "gloss": "g"}))
    assert rec is None and "static check failed" in why
