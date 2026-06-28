"""Tests for closed-loop execution + bounded replanning."""
from iap_downstream.causal_graph import Atom, GroundAction, State, Threshold
from iap_downstream.env_adapter import Env, StepResult
from iap_downstream.executor import run_episode
from iap_downstream.mock_env import MockMCDrift, build_mock_graphs

GRAPHS = build_mock_graphs()


def _run(method, task, condition):
    env = MockMCDrift(task, condition, seed=0)
    return run_episode(env, env.goal_of(task), GRAPHS[method], R_max=3, step_budget=100)


def test_after_succeeds_in_drift():
    assert _run("after", "craftBoat", "drift").success
    assert _run("after", "mineDiamond", "drift").success


def test_before_fails_in_drift():
    assert not _run("before", "craftBoat", "drift").success
    assert not _run("before", "mineDiamond", "drift").success


def test_origin_succeeds_for_all_beliefs():
    for m in ("before", "after", "oracle"):
        assert _run(m, "craftBoat", "origin").success
        assert _run(m, "mineDiamond", "origin").success


def test_minus_boundary_fails_on_depth():
    # wrong (too shallow) depth belief -> mining fails in drift
    assert not _run("minus_boundary", "mineDiamond", "drift").success
    # but the boolean water gate is robust to it -> boat still succeeds
    assert _run("minus_boundary", "craftBoat", "drift").success


class _FlakyEnv(MockMCDrift):
    """Mining fails once (transient), then works - to exercise replanning."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._failed_once = False

    def step(self, ga: GroundAction) -> StepResult:
        if ga.name == "mine_diamond" and not self._failed_once:
            self._failed_once = True
            return StepResult(False, "transient failure")
        return super().step(ga)


def test_replan_recovers_from_transient_failure():
    env = _FlakyEnv("mineDiamond", "drift", seed=0)
    res = run_episode(env, env.goal_of("mineDiamond"), GRAPHS["after"], R_max=3, step_budget=100)
    assert res.success
    assert res.replans >= 1
