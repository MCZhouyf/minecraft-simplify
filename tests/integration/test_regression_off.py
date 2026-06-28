"""Stage-6 integration: verification_mode='off' must be byte-for-byte baseline.

Constructs a real ADAM instance (no controller run, no LLM calls) and checks
every TCPG hook is inert; then a baseline action smoke on the shared env
confirms the world still behaves vanilla through the patched code path.
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from tests.conftest import reset_with, run_action, count_of   # noqa: E402

pytestmark = pytest.mark.integration


def _make_adam(mode):
    from Adam.ADAM import ADAM
    return ADAM(mc_port=int(os.environ["IAP_MC_PORT"]),
                game_server_port=int(os.environ.get("IAP_MF_PORT", "3000")) + 7,
                prompt_folder_path=str(REPO / "prompts"),
                verification_mode=mode)


def test_off_mode_hooks_are_inert():
    adam = _make_adam("off")
    assert adam.verification_mode == "off"
    assert adam._tcpg_runtime() is None
    assert adam._tcpg_gate_text() == ""
    assert adam._tcpg_next_graph_action({"oak_log": 4}) is None   # always LLM path
    adam._tcpg_on_action("craftPlanks", {"oak_planks": 4}, {})    # must no-op
    assert adam._tcpg_rt is None


def test_adam_original_mode_also_inert():
    adam = _make_adam("adam_original")
    assert adam._tcpg_runtime() is None and adam._tcpg_gate_text() == ""


def test_tcpg_mode_graph_planning_engages():
    adam = _make_adam("tcpg")
    rt = adam._tcpg_runtime()
    assert rt is not None and adam._tcpg_gate_text() == ""        # empty until write-back
    adam.goal = (["oak_planks"], [])
    nxt = adam._tcpg_next_graph_action({"oak_log": 1})
    assert nxt == "craftPlanks"                                   # zero-LLM plan
    assert adam._tcpg_next_graph_action({"oak_planks": 4}) is None  # goal satisfied


def test_baseline_action_still_vanilla(env):
    reset_with(env, {"oak_log": 1})
    obs = run_action(env, "craftPlanks")
    assert count_of(obs, "oak_planks") >= 1
