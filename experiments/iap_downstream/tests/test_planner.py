"""Tests for the goal-regression planner, validator and BFS fallback."""
from iap_downstream.causal_graph import Atom, State, Threshold
from iap_downstream.mock_env import build_mock_graphs
from iap_downstream.planner import bfs_plan, plan, regress_plan, validate_plan

GRAPHS = build_mock_graphs()
EMPTY = State(atoms=set(), nums={"y_level": 0, "water_radius": 99})
BOAT_GOAL = (Atom("have", ("boat",)),)
DIAMOND_GOAL = (Atom("have", ("diamond",)),)


def test_regress_after_boat_includes_water_step():
    p = regress_plan(BOAT_GOAL, GRAPHS["after"], EMPTY)
    assert p is not None
    names = [a.name for a in p]
    assert "move_to_water" in names and "craft_boat" in names
    assert names.index("move_to_water") < names.index("craft_boat")
    assert validate_plan(p, BOAT_GOAL, EMPTY)


def test_regress_before_omits_gate_step():
    p = regress_plan(BOAT_GOAL, GRAPHS["before"], EMPTY)
    assert p is not None
    names = [a.name for a in p]
    # stale graph does not know about the water gate -> never moves to water
    assert "move_to_water" not in names
    # but it is still a *valid* plan under the stale belief
    assert validate_plan(p, BOAT_GOAL, EMPTY)


def test_regress_diamond_binds_threshold_value():
    p = regress_plan(DIAMOND_GOAL, GRAPHS["after"], EMPTY)
    assert p is not None
    desc = [a for a in p if a.name == "descend"]
    assert len(desc) == 1
    # planner must descend to the believed operational depth (-8)
    assert dict(desc[0].sets)["y_level"] == -8
    assert validate_plan(p, DIAMOND_GOAL, EMPTY)


def test_minus_boundary_uses_wrong_depth():
    p = regress_plan(DIAMOND_GOAL, GRAPHS["minus_boundary"], EMPTY)
    desc = [a for a in p if a.name == "descend"]
    assert dict(desc[0].sets)["y_level"] == -5  # the wrong, too-shallow belief


def test_bfs_fallback_matches():
    p = bfs_plan(BOAT_GOAL, GRAPHS["after"], EMPTY)
    assert p is not None
    assert validate_plan(p, BOAT_GOAL, EMPTY)


def test_plan_entrypoint_returns_valid():
    for g in GRAPHS.values():
        for goal in (BOAT_GOAL, DIAMOND_GOAL):
            p = plan(goal, g, EMPTY)
            assert p is not None
            assert validate_plan(p, goal, EMPTY)
