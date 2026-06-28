"""Offline tests for stage 4 proposer (contract K4). No LLM key needed —
the llm parameter is a plain callable, mocked here."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.proposer import (Candidate, candidates_from_success,   # noqa: E402
                                parse_json_array, propose_from_failure, validate)

EVENT = json.loads((REPO / "tests/fixtures/failure_events/C2.json").read_text())

LEGAL = {"dimension": "capability", "target": "held_tool", "property": "tier",
         "comparator": ">=", "value": "diamond"}
ILLEGAL_TARGET = {"dimension": "capability", "target": "mana_level",
                  "property": "x", "comparator": ">=", "value": 1}
ILLEGAL_DIM = {"dimension": "resource", "target": "held_tool",
               "property": "tier", "comparator": ">=", "value": "iron"}


class ScriptedLLM:
    """Returns scripted replies in order; records prompts."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "[]"


# --------------------------------------------------------------- cid stability
def test_cid_stable_and_pools_independent():
    a = Candidate("mineGoldOre", "capability", "held_tool", "tier", ">=", "diamond")
    b = Candidate("mineGoldOre", "context", "held_tool", "tier", ">=", "diamond",
                  source="success_precondition", n_pos=5)
    assert a.cid == b.cid                  # dimension/source/pools don't enter cid
    c = Candidate("mineGoldOre", "capability", "held_tool", "tier", ">=", "iron")
    assert a.cid != c.cid


# --------------------------------------------------------------- validation
def test_validate_whitelist_and_structure():
    ok, _ = validate(Candidate("a", **LEGAL))
    assert ok
    ok, why = validate(Candidate("a", **ILLEGAL_TARGET))
    assert not ok and "unknown target" in why or "not whitelisted" in why
    ok, why = validate(Candidate("a", **ILLEGAL_DIM))
    assert not ok and "not whitelisted" in why
    ok, why = validate(Candidate("a", "capability", "held_tool", "tier",
                                 ">=", "obsidian"))
    assert not ok and "tier" in why


# --------------------------------------------------------------- JSON parsing
def test_parse_json_array_strips_fences_and_prose():
    text = "Step1 blah\n```json\n[" + json.dumps(LEGAL) + "]\n```\nStep3 prose"
    arr = parse_json_array(text)
    assert arr == [LEGAL]
    with pytest.raises(ValueError):
        parse_json_array("no array here")


def test_parse_json_array_repairs_common_llm_json_slips():
    text = """```json
[
  {"dimension" "capability", "target": "held_tool", "property": "tier",
   "comparator": ">=", "value": "diamond",}
]
```"""
    assert parse_json_array(text) == [LEGAL]
    text2 = """[
      {"dimension": "context", "target": "y_level", "property": "y",
       "comparator": "<=", "value" -10}
    ]"""
    assert parse_json_array(text2)[0]["value"] == -10


# --------------------------------------------------------------- retry loop
def test_propose_retries_illegal_then_succeeds():
    first = json.dumps([LEGAL, ILLEGAL_TARGET])
    second = json.dumps([{"dimension": "context", "target": "y_level",
                          "property": "y", "comparator": "<=", "value": -16}])
    llm = ScriptedLLM([first, second])
    cands = propose_from_failure(EVENT, llm=llm)
    assert len(llm.prompts) == 2                       # one retry happened
    assert "ILLEGAL" in llm.prompts[1]                 # error feedback appended
    cids = {c.target for c in cands}
    assert cids == {"held_tool", "y_level"}            # legal kept + retry merged


def test_propose_survives_garbage_and_dedupes():
    llm = ScriptedLLM(["total garbage", json.dumps([LEGAL, LEGAL])])
    cands = propose_from_failure(EVENT, llm=llm, expand=False)  # isolate dedup
    assert len(cands) == 1 and cands[0].source == "tcpg"


def test_propose_gives_up_after_retries():
    llm = ScriptedLLM(["junk", "junk", "junk", "junk"])
    cands = propose_from_failure(EVENT, llm=llm, max_retries=2)
    assert cands == [] and len(llm.prompts) == 3       # initial + 2 retries


# --------------------------------------------------------------- success branch
def test_candidates_from_success_filters():
    pre = [LEGAL,
           {"dimension": "resource", "target": "inventory_count",
            "property": "coal", "comparator": ">=", "value": 1},
           ILLEGAL_TARGET]
    skip = Candidate("mineGoldOre", **LEGAL).cid
    out = candidates_from_success("mineGoldOre", pre, skip_cids=[skip])
    assert [c.target for c in out] == ["inventory_count"]   # skipped + illegal dropped
    assert all(c.source == "success_precondition" for c in out)


# --------------------------------------------------------------- neighbor expansion
def test_neighbor_expansion_reads_signature_order():
    """C2 root cause fix: LLM proposes vanilla iron; signature-induced expansion
    adds the order-adjacent diamond so verification can reach the drifted value.
    Order comes from schema.ordered_domains, NOT hardcoded in the proposer."""
    from Adam.tcpg.proposer import expand_neighbors, _neighbors_for
    iron = Candidate("mineGoldOre", "capability", "held_tool", "tier", ">=", "iron")
    vals = {s.value for s in _neighbors_for(iron)}
    assert vals == {"stone", "diamond"}
    out = expand_neighbors([iron])
    assert {c.value for c in out} == {"iron", "stone", "diamond"}
    assert all(c.source in ("tcpg", "neighbor") for c in out)
    # endpoints of the declared order have a single neighbour
    wood = Candidate("a", "capability", "held_tool", "tier", ">=", "wooden")
    assert {s.value for s in _neighbors_for(wood)} == {"stone"}
    nether = Candidate("a", "capability", "held_tool", "tier", ">=", "netherite")
    assert {s.value for s in _neighbors_for(nether)} == {"diamond"}
    # 'golden' is off-ladder (not in ordered_domains) -> no neighbours
    gold = Candidate("a", "capability", "held_tool", "tier", ">=", "golden")
    assert _neighbors_for(gold) == []


def test_no_numeric_magic_expansion():
    """Continuous numeric thresholds are NOT enumerated (no magic step size);
    their error is absorbed by the posterior's delta-margin decision."""
    from Adam.tcpg.proposer import _neighbors_for
    cnt = Candidate("craftFurnace", "resource", "inventory_count", "coal", ">=", 1)
    y = Candidate("a", "context", "y_level", "y", "<=", -16)
    assert _neighbors_for(cnt) == [] and _neighbors_for(y) == []


def test_expansion_is_signature_driven_not_target_hardcoded(monkeypatch):
    """Proof of environment-agnosticism: inject a NEW ordered domain into the
    signature and a primitive using it; expansion must work with zero proposer
    code changes."""
    import Adam.tcpg.predicates as PR
    sig = dict(PR._SCHEMA)
    sig["ordered_domains"] = dict(sig.get("ordered_domains", {}),
                                  size_enum=["small", "medium", "large"])
    sig["primitives"] = dict(sig["primitives"],
                             container_size={"property_kind": "fixed:size",
                                             "value_type": "size_enum",
                                             "comparators": ["="],
                                             "evaluator": "js"})
    sig["whitelist"] = dict(sig["whitelist"],
                            procedure=sig["whitelist"]["procedure"] + ["container_size"])
    monkeypatch.setattr(PR, "_SCHEMA", sig)
    from Adam.tcpg.proposer import _neighbors_for
    c = Candidate("openChest", "procedure", "container_size", "size", "=", "medium")
    assert {s.value for s in _neighbors_for(c)} == {"small", "large"}


def test_neighbor_expansion_propagates_through_propose():
    """End to end: scripted LLM emits ONLY vanilla iron -> proposer returns
    diamond among candidates via signature-induced expansion (default on)."""
    import json
    llm = ScriptedLLM([json.dumps([{"dimension": "capability", "target": "held_tool",
                                     "property": "tier", "comparator": ">=",
                                     "value": "iron"}])])
    cands = propose_from_failure(EVENT, llm=llm)
    assert any(c.target == "held_tool" and c.value == "diamond" for c in cands)
    llm2 = ScriptedLLM([json.dumps([{"dimension": "capability", "target": "held_tool",
                                     "property": "tier", "comparator": ">=",
                                     "value": "iron"}])])
    bare = propose_from_failure(EVENT, llm=llm2, expand=False)
    assert {c.value for c in bare} == {"iron"}
