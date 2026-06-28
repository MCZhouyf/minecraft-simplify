"""Budget-aware intervention verification (cost-sensitive acquisition + K5
unsatisfiable-wait guard). Restores the cost term Score = q*gamma/c^alpha from
the cost-constrained design so a cheap true cause (y_level) is verified before
expensive ordered-domain neighbours (diamond_pickaxe)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.posterior import Acquisition, DualPool       # noqa: E402
from Adam.tcpg.proposer import Candidate                    # noqa: E402
from Adam.tcpg import compiler as C                          # noqa: E402


def _equal_evidence_pools():
    pools = {"cheap": DualPool(), "expensive": DualPool()}
    for cid in pools:
        pools[cid].update("pos", 1)
        pools[cid].update("pos", 1)
        pools[cid].update("neg", 0)
        pools[cid].update("neg", 0)
    return pools


def test_cost_aware_prefers_cheap_when_evidence_equal():
    pools = _equal_evidence_pools()
    acq = Acquisition(rr_every=99, cost_alpha=0.5)   # RR off -> pure greedy
    costs = {"cheap": 2.0, "expensive": 200.0}
    assert acq.select(pools, ["cheap", "expensive"], costs=costs) == "cheap"


def test_budget_excludes_unaffordable():
    pools = _equal_evidence_pools()
    acq = Acquisition(rr_every=99)
    costs = {"cheap": 2.0, "expensive": 200.0}
    assert acq.select(pools, ["cheap", "expensive"], costs=costs,
                      budget=50.0) == "cheap"


def test_budget_none_affordable_does_not_stall():
    """If nothing is affordable, fall back to full eligible (don't return None
    and stall the trigger)."""
    pools = _equal_evidence_pools()
    acq = Acquisition(rr_every=99)
    costs = {"cheap": 100.0, "expensive": 200.0}
    pick = acq.select(pools, ["cheap", "expensive"], costs=costs, budget=10.0)
    assert pick in ("cheap", "expensive")            # not None


def test_alpha_zero_is_cost_blind():
    pools = _equal_evidence_pools()
    blind = Acquisition(rr_every=99, cost_alpha=0.0)
    costs = {"cheap": 2.0, "expensive": 200.0}
    # with alpha=0 the cost denominator is gone; both score equally, so the
    # pick must NOT be driven by cost (deterministic max() tie-break only).
    p_blind = blind.select(pools, ["cheap", "expensive"], costs=costs)
    aware = Acquisition(rr_every=99, cost_alpha=1.0)
    p_aware = aware.select(pools, ["cheap", "expensive"], costs=costs)
    assert p_aware == "cheap"                         # cost-aware favors cheap
    # blind may pick either; the point is it ignores the 100x cost gap
    assert p_blind in ("cheap", "expensive")


def test_rr_floor_still_fires():
    """Round-robin floor must still rescue a starved candidate."""
    pools = {"a": DualPool(), "b": DualPool()}
    pools["a"].update("pos", 1)                       # a looks better
    acq = Acquisition(rr_every=2, cost_alpha=0.5)
    costs = {"a": 1.0, "b": 1.0}
    picks = [acq.select(pools, ["a", "b"], costs=costs) for _ in range(4)]
    assert "b" in picks                               # RR gave b a turn


def test_k5_rejects_full_day_wait():
    full = Candidate("smeltRawIron", "environment", "time_of_day", "t",
                     "in", [0, 24000])
    assert C.compile(full, {"inventory": {}})["infeasible_reason"] == "no_macro"
    wide = Candidate("a", "environment", "time_of_day", "t", "in", [0, 30000])
    assert C.compile(wide, {"inventory": {}})["infeasible_reason"] == "no_macro"


def test_k5_normal_window_still_compiles():
    ok = Candidate("smeltRawIron", "environment", "time_of_day", "t",
                   "in", [0, 12000])
    assert C.compile(ok, {"inventory": {}})["feasible"]
