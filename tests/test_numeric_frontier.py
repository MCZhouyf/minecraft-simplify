"""Offline tests: numeric frontier expansion (recall) + boundary intervention
(exact-set verification) for monotone numeric thresholds. Parallels the ordered
-enum neighbor expansion. Fully offline: proposer + compiler + freedo realize."""
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

from Adam.tcpg import compiler as C                                  # noqa: E402
from Adam.tcpg import predicates as P                                # noqa: E402
from Adam.tcpg.ccg import CCG                                        # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime                            # noqa: E402
from Adam.tcpg.proposer import (Candidate, expand_numeric_frontier,  # noqa: E402
                                 _numeric_frontier_for, validate,
                                 expand_time_complements,
                                 normalize_candidate)


# --------------------------------------------------------------- schema flags
def test_schema_declares_monotone_and_exact_settable():
    prim = P.schema()["primitives"]
    assert prim["inventory_count"].get("monotone") == "up"
    assert prim["inventory_count"].get("exact_settable") is True
    assert prim["y_level"].get("monotone") == "down"
    assert prim["y_level"].get("exact_settable") is True
    # non-monotone / non-settable targets must NOT carry these flags
    assert "monotone" not in prim["held_item"]
    assert "monotone" not in prim["station_type"]


# ------------------------------------------------------ frontier (recall) side
def _inv_count(value, prop="oak_planks", comp=">="):
    return Candidate(action="craftFence", dimension="resource",
                     target="inventory_count", property=prop,
                     comparator=comp, value=value)


def test_frontier_up_generates_above_failure_value():
    # LLM proposed oak_planks>=6 (== failure value); true threshold is 8 > 6.
    obs = {"inventory": {"oak_planks": 6, "stick": 2}}
    sibs = _numeric_frontier_for(_inv_count(6), obs, k=4)
    vals = sorted(s.value for s in sibs)
    assert vals == [7, 8, 9, 10]               # v+1 .. v+K, the true 8 included
    assert all(s.comparator == ">=" for s in sibs)
    assert all(s.source == "frontier" for s in sibs)


def test_frontier_anchored_on_observed_value_not_candidate_value():
    # candidate value (4) differs from observed inventory (6): anchor on obs.
    obs = {"inventory": {"oak_planks": 6}}
    sibs = _numeric_frontier_for(_inv_count(4), obs, k=3)
    assert sorted(s.value for s in sibs) == [7, 8, 9]


def test_frontier_down_for_y_level_covers_c4():
    # mineDiamondOre failed at the surface (y=0); true gate y<=-10 is below.
    c = Candidate(action="mineDiamondOre", dimension="context", target="y_level",
                  property="y", comparator="<=", value=0)
    obs = {"position": {"x": 0, "y": 0, "z": 0}}
    sibs = _numeric_frontier_for(c, obs, k=12)
    vals = sorted(s.value for s in sibs)
    assert vals[0] == -12 and vals[-1] == -1   # v-K .. v-1
    assert -10 in vals                         # the true threshold is reachable
    assert all(s.comparator == "<=" for s in sibs)


def test_frontier_down_from_round3_anchor_covers_c4():
    c = Candidate(action="mineDiamondOre", dimension="context", target="y_level",
                  property="y", comparator="<=", value=22)
    obs = {"position": {"x": 0, "y": -6, "z": 0}}
    sibs = _numeric_frontier_for(c, obs, k=8)
    vals = sorted(s.value for s in sibs)
    assert vals == [-14, -13, -12, -11, -10, -9, -8, -7]


def test_frontier_skips_non_monotone_or_unsettable_targets():
    # held_item is not a monotone numeric -> no frontier
    c = Candidate(action="a", dimension="capability", target="held_item",
                  property="type", comparator="=", value="pickaxe")
    assert _numeric_frontier_for(c, {"inventory": {}}, k=4) == []
    # y_level with no readable position -> unknown value -> no frontier
    yc = Candidate(action="mineDiamondOre", dimension="context", target="y_level",
                   property="y", comparator="<=", value=-5)
    assert _numeric_frontier_for(yc, {"inventory": {}}, k=4) == []
    # inventory_count with the item ABSENT is value 0 (not unknown) -> DOES expand
    assert len(_numeric_frontier_for(_inv_count(6), {"inventory": {}}, k=4)) == 4


def test_expand_numeric_frontier_dedups_and_validates():
    obs = {"inventory": {"oak_planks": 6}}
    base = [_inv_count(6)]
    out = expand_numeric_frontier(base, obs, k=4)
    # original + 4 frontier siblings, all unique, all schema-legal
    assert len(out) == 5
    assert all(validate(c)[0] for c in out)


def test_time_complement_expands_day_to_night():
    day = Candidate(action="smeltRawIron", dimension="environment",
                    target="time_of_day", property="time",
                    comparator="in", value=[0, 12000])
    out = expand_time_complements([day])
    vals = sorted(tuple(c.value) for c in out)
    assert (0, 12000) in vals
    assert (12000, 24000) in vals


def test_time_full_day_expands_to_day_and_night():
    full = Candidate(action="smeltRawIron", dimension="environment",
                     target="time_of_day", property="time",
                     comparator="in", value=[0, 24000])
    out = expand_time_complements([full])
    vals = sorted(tuple(c.value) for c in out)
    assert (0, 12000) in vals
    assert (12000, 24000) in vals


def test_time_of_day_normalizes_nighttime_token():
    raw = Candidate(action="smeltRawIron", dimension="environment",
                    target="time_of_day", property="time",
                    comparator="in", value="nighttime")
    c = normalize_candidate(raw)
    assert c.value == [12000, 24000]
    assert C.compile(c, state={})["feasible"]


# ------------------------------------------------- boundary (verification) side
def test_boundary_compile_emits_exact_set_for_inventory_frontier():
    c = _inv_count(8)
    c.source = "frontier"
    k5 = C.compile(c, state={"inventory": {"oak_planks": 6, "chest": 1}})
    assert k5["feasible"]
    # I+ sets the tested item EXACTLY to 8, I- to 7 (n-1 for '>='); co-inputs
    # (e.g. fence sticks) may be topped up alongside for isolation.
    def _sc(plan):
        return {s["args"]["name"]: s["args"]["count"]
                for s in plan if s["primitive"] == "set_count"}
    assert _sc(k5["plan_plus"]).get("oak_planks") == 8
    assert _sc(k5["plan_minus"]).get("oak_planks") == 7
    # inventory_count stays sim-verifiable -> flat cost tier (automatic)
    assert k5["sim_verifiable"] is True


def test_boundary_compile_down_direction_for_y_level():
    c = Candidate(action="mineDiamondOre", dimension="context", target="y_level",
                  property="y", comparator="<=", value=-10, source="frontier")
    k5 = C.compile(c, state={"agent_y": 0.0})
    # I+ at y=-10 (satisfies <=-10), I- at y=-9 (violates) -> n+1 for '<='
    assert k5["plan_plus"] == [C.call("set_y", y=-10.0)]
    assert k5["plan_minus"] == [C.call("set_y", y=-9.0)]
    assert k5["sim_verifiable"] is False        # y_level keeps full cost


def test_non_frontier_inventory_uses_legacy_template_unchanged():
    # a normal (non-frontier) inventory_count candidate must NOT use set_count
    c = _inv_count(8)                           # source defaults to "tcpg"
    k5 = C.compile(c, state={"inventory": {"oak_planks": 6, "chest": 1}})
    prims = {s["primitive"] for s in k5["plan_plus"] + k5["plan_minus"]}
    assert "set_count" not in prims             # legacy acquire/deposit path


# ------------------------------------------ end-to-end identifiability (freedo)
class _Env:
    def __init__(self, inv):
        self.reset_opts = []
        self.snap = {"agent.x": 0.0, "agent.y": 64.0, "agent.z": 0.0,
                     "inventory": dict(inv)}

    def reset(self, options=None):
        self.reset_opts.append(options or {})
        return []

    def step(self, code):
        return [[0, {}]]


def _rt(env, monkeypatch):
    monkeypatch.setattr(P, "state_snapshot", lambda e, timeout=60: dict(env.snap))
    return TcpgRuntime(env, ccg=CCG.init_default(), mode="freedo_oracle",
                       execute_action=lambda a: True)


def test_freedo_set_count_sets_inventory_exactly(monkeypatch):
    env = _Env({"oak_planks": 6, "stick": 2})
    rt = _rt(env, monkeypatch)
    ok = rt._freedo([C.call("set_count", name="oak_planks", count=8,
                            special="exact")], "cid")
    assert ok
    inv = env.reset_opts[-1]["inventory"]
    assert inv["oak_planks"] == 8               # exact override, not 6+8
    assert inv["stick"] == 2                     # other items preserved


def test_boundary_uniquely_identifies_threshold(monkeypatch):
    """Only n == theta* yields pos-success / neg-fail under a monotone gate."""
    GATE = 8                                     # hidden true gate: oak_planks>=8
    env = _Env({"oak_planks": 6})
    rt = _rt(env, monkeypatch)
    verdicts = {}
    for n in (7, 8, 9, 10):                      # the frontier candidates
        c = _inv_count(n); c.source = "frontier"
        k5 = C.compile(c, state={"inventory": {"oak_planks": 6, "chest": 1}})
        rt._freedo(k5["plan_plus"], "cid")
        pos = env.reset_opts[-1]["inventory"]["oak_planks"] >= GATE
        rt._freedo(k5["plan_minus"], "cid")
        neg = env.reset_opts[-1]["inventory"]["oak_planks"] >= GATE
        verdicts[n] = (pos, neg)
    # n=8 is the only causal contrast (pos True, neg False)
    assert verdicts[8] == (True, False)
    assert verdicts[7] == (False, False)         # below threshold: both fail
    assert verdicts[9] == (True, True)           # above: boundary n-1=8 passes
    assert verdicts[10] == (True, True)
    accepted = [n for n, (p, q) in verdicts.items() if p and not q]
    assert accepted == [8]
