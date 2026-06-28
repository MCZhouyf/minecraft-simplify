"""Tests for the full Figure-2 loop: online discovery + downstream completion."""
import random

from iap_downstream.agent import run_iap_episode
from iap_downstream.calibration import calibrate
from iap_downstream.causal_graph import Threshold
from iap_downstream.mock_env import MockMCDrift, build_mock_graphs
from iap_downstream.posterior import DualPool
from iap_downstream.proposer import MockProposer

STALE = build_mock_graphs()["before"]
PROP = MockProposer()


def _episode(task, cond, seed=0, posterior_cfg=None):
    env = MockMCDrift(task, cond, seed)
    return run_iap_episode(env, task, STALE, PROP, posterior_cfg=posterior_cfg, seed=seed)


# --- full loop --------------------------------------------------------------
def test_drift_craftboat_discovers_water_and_completes():
    r = _episode("craftBoat", "drift")
    assert r.completed
    assert any("water_radius" in g for _, g in r.discovered)
    assert r.interventions > 0  # discovery cost the agent interventions


def test_drift_minediamond_discovers_operational_depth():
    r = _episode("mineDiamond", "drift")
    assert r.completed
    # operational boundary is -8 (Route B), not the nominal -10
    assert any("y_level" in g and "-8" in g for _, g in r.discovered)


def test_origin_completes_without_discovery():
    for task in ("craftBoat", "mineDiamond"):
        r = _episode(task, "origin")
        assert r.completed
        assert r.discovered == []      # nothing to discover in origin
        assert r.interventions == 0


def test_point_mode_also_completes_on_clean_task():
    # -dual_pool ablation still finds the gate here (spurious candidates fail the
    # positive side too), consistent with the paper's null-on-clean finding.
    r = _episode("craftBoat", "drift", posterior_cfg={"mode": "point"})
    assert r.completed


# --- calibration internals --------------------------------------------------
def test_calibration_rejects_resources_then_nota_finds_gate():
    env = MockMCDrift("craftBoat", "drift", seed=0)
    # put the agent in the post-failure state: it has planks, far from water
    env.world.atoms.add(("have", ("planks",)))
    res = calibrate("craft_boat", env, PROP, seed=0)
    assert res.gate == Threshold("water_radius", "<=", 3)
    assert res.used_nota is True
    labels = dict(res.tested)
    assert labels.get("extra_planks") == "rejected"
    assert labels.get("crafting_table") == "rejected"


# --- posterior contrast (dual vs point) -------------------------------------
def test_dual_rejects_weak_contrast_point_accepts():
    rng = random.Random(0)
    # positive side and negative side BOTH succeed -> no contrast (the C4 case)
    dual = DualPool(mode="dual")
    point = DualPool(mode="point")
    for _ in range(5):
        dual.update("pos", True); dual.update("neg", True)
        point.update("pos", True); point.update("neg", True)
    assert dual.decide(rng) == "rejected"     # contrast test catches it
    assert point.decide(rng) == "accepted"    # positive-only is fooled


def test_dual_accepts_strong_contrast():
    rng = random.Random(0)
    d = DualPool(mode="dual")
    for _ in range(5):
        d.update("pos", True)
        d.update("neg", False)
    assert d.decide(rng) == "accepted"
