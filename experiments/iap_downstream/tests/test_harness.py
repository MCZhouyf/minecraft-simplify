"""End-to-end harness tests: the full sweep and its attribution invariants."""
from iap_downstream.harness import downstream_success, run_sweep, sanity_checks
from iap_downstream.mock_env import build_mock_graphs, mock_env_factory, mock_tasks

GRAPHS = build_mock_graphs()
TASKS = mock_tasks()


def _sweep(seeds=5):
    return run_sweep(mock_env_factory, GRAPHS, TASKS, n_seeds=seeds)


def _cell(rows, method, paper_id, condition):
    for r in rows:
        if r.method == method and r.paper_id == paper_id and r.condition == condition:
            return r
    raise KeyError((method, paper_id, condition))


def test_drift_before_zero_after_one():
    rows = _sweep()
    for pid in ("C1", "C3"):
        assert _cell(rows, "before", pid, "drift").success_rate == 0.0
        assert _cell(rows, "after", pid, "drift").success_rate == 1.0
        assert _cell(rows, "oracle", pid, "drift").success_rate == 1.0


def test_origin_unaffected_by_writeback():
    rows = _sweep()
    for pid in ("C1", "C3"):
        assert _cell(rows, "before", pid, "origin").success_rate == 1.0
        assert _cell(rows, "after", pid, "origin").success_rate == 1.0


def test_minus_boundary_degrades_depth_task():
    rows = _sweep()
    # parameter error kills the depth task in drift, boolean water task survives
    assert _cell(rows, "minus_boundary", "C3", "drift").success_rate == 0.0
    assert _cell(rows, "minus_boundary", "C1", "drift").success_rate == 1.0


def test_sanity_checks_pass_for_after_vs_before():
    # restrict to the before/after/oracle graphs the invariants are about
    g = {k: GRAPHS[k] for k in ("before", "after", "oracle")}
    rows = run_sweep(mock_env_factory, g, TASKS, n_seeds=5)
    assert sanity_checks(rows) == []


def test_ci_present_and_ordered():
    r = downstream_success(mock_env_factory, "after", "craftBoat", "C1", "drift", GRAPHS["after"], n_seeds=5)
    assert r.ci_low <= r.success_rate <= r.ci_high
    assert r.k == 5 and r.n == 5
