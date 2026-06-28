"""IaP downstream-success harness (Stage B).

Public API:
    from iap_downstream import run_sweep, downstream_success, sanity_checks
    from iap_downstream import CausalGraph, Action, Atom, Threshold
    from iap_downstream import Env, StepResult
    from iap_downstream.metrics import write_downstream_csv, to_table2, to_table3_success, to_table4_success
"""
from .causal_graph import Action, Atom, CausalGraph, GroundAction, State, Threshold, ground
from .env_adapter import Env, StepResult
from .executor import EpisodeResult, run_episode
from .harness import downstream_success, run_sweep, sanity_checks
from .metrics import (
    DownstreamRow,
    to_table2,
    to_table3_success,
    to_table4_success,
    wilson_ci,
    write_downstream_csv,
)
from .planner import bfs_plan, plan, regress_plan, validate_plan
from .posterior import DualPool
from .proposer import Candidate, MockProposer, Proposer
from .nota import enumerate_candidates
from .acquisition import order
from .calibration import CalibrationResult, calibrate
from .agent import AgentResult, run_iap_episode

__all__ = [
    "Action",
    "Atom",
    "CausalGraph",
    "GroundAction",
    "State",
    "Threshold",
    "ground",
    "Env",
    "StepResult",
    "EpisodeResult",
    "run_episode",
    "downstream_success",
    "run_sweep",
    "sanity_checks",
    "DownstreamRow",
    "wilson_ci",
    "write_downstream_csv",
    "to_table2",
    "to_table3_success",
    "to_table4_success",
    "plan",
    "regress_plan",
    "validate_plan",
    "bfs_plan",
    "DualPool",
    "Candidate",
    "MockProposer",
    "Proposer",
    "enumerate_candidates",
    "order",
    "CalibrationResult",
    "calibrate",
    "AgentResult",
    "run_iap_episode",
]
