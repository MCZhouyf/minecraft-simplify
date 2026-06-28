"""Stage 6 offline tests: dual-pool posterior vs the paper's computed Table D.1."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.posterior import Acquisition, DualPool, scarce_side  # noqa: E402

M = 200_000


def perfect(n):
    p = DualPool()
    for _ in range(n):
        p.update("pos", 1)
        p.update("neg", 0)
    return p


def test_matches_table_d1():
    """Perfect separation, delta=0.3: q(3)=0.888 q(4)=0.947 q(5)=0.975 (+-0.02);
    these are the exact numbers in paper App. D.2 / Table D.1."""
    for n, expect in [(3, 0.888), (4, 0.947), (5, 0.975)]:
        q, g = perfect(n).stats(delta=0.3, M=M, seed=0)
        assert abs(q - expect) < 0.02, f"n={n}: q={q}"
    assert perfect(4).stats(M=M, seed=0)[1] > perfect(3).stats(M=M, seed=0)[1]


def test_decision_thresholds_and_n_min():
    assert perfect(3).decide(M=M, seed=0) == "undecided"   # q=.888 < .9
    assert perfect(4).decide(M=M, seed=0) == "accepted"    # q=.947, n_eff=4
    null = DualPool()
    for _ in range(5):
        null.update("pos", 1)
        null.update("neg", 1)                              # no effect
    assert null.decide(M=M, seed=0) == "rejected"
    thin = perfect(10)
    thin.n_neg = thin.k_neg = 0                            # one-sided only
    assert thin.n_eff == 0 and thin.decide(M=M, seed=0) == "undecided"


def test_point_mode_ignores_negative_pool_and_mc_posterior():
    p = DualPool()
    for _ in range(4):
        p.update("pos", 1)
    assert p.n_eff == 0
    assert p.decide(M=M, seed=0) == "undecided"
    assert p.decide(M=M, seed=0, mode="point") == "accepted"

    pending = DualPool()
    assert pending.decide(M=M, seed=0, mode="point") == "pending"

    low = DualPool()
    for _ in range(4):
        low.update("pos", 0)
    assert low.decide(M=M, seed=0, mode="point") == "rejected"


def test_point_mode_accepts_candidate_dual_pool_rejects_with_negative_evidence():
    p = DualPool()
    for _ in range(4):
        p.update("pos", 1)
    for y in (1, 1, 0):
        p.update("neg", y)

    assert p.decide(M=M, seed=0, n_min=3, tau_rej=0.5) == "rejected"
    assert p.decide(M=M, seed=0, n_min=3, tau_rej=0.5, mode="point") == "accepted"


def test_invalidate_last_restores_pool():
    p = perfect(4)
    p.update("pos", 1)
    assert (p.n_pos, p.k_pos) == (5, 5)
    assert p.invalidate_last() and (p.n_pos, p.k_pos) == (4, 4)
    assert not p.invalidate_last()                          # nothing pending


def test_scarce_side():
    p = DualPool()
    p.update("pos", 1)
    assert scarce_side(p) == "neg"
    p.update("neg", 0)
    assert scarce_side(p) == "pos"                          # tie -> pos


def test_acquisition_greedy_with_floor():
    pools = {"strong": perfect(2), "weak": DualPool(), "mid": DualPool()}
    pools["weak"].update("pos", 1)
    pools["mid"].update("pos", 1)
    pools["mid"].update("neg", 0)
    acq = Acquisition(rr_every=3, M=20_000, seed=1)
    picks = [acq.select(pools, ["strong", "weak", "mid"]) for _ in range(6)]
    assert picks[0] == "strong" and picks[1] == "strong"    # greedy
    assert picks[2] != "strong"                             # floor kicks in
    assert set(picks) == {"strong", "weak", "mid"}          # everyone served
    # pigeonhole floor: each candidate selected >= floor(6/3)/... at least once
    assert all(acq.counts.get(c, 0) >= 1 for c in pools)
    assert acq.select(pools, []) is None
