import csv
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.modules.setdefault("javascript", types.ModuleType("javascript"))
sys.modules["javascript"].require = lambda *a, **k: None

from Adam.tcpg.proposer import Candidate  # noqa: E402
from Adam.tcpg.runtime import TcpgRuntime  # noqa: E402
from experiments import run_round3_matrix as M  # noqa: E402
from experiments import runner as R  # noqa: E402


def test_desired_matrix_shape_and_ids():
    rows = M.desired_matrix()
    assert len(rows) == 120
    assert M.MatrixRun("R2", "tcpg", 0, "R2", "R2_tcpg_minimal_s0") in rows
    assert M.MatrixRun("R2", "tcpg", 0, "R2_nota",
                       "R2_nota_tcpg_minimal_s0") in rows
    assert M.MatrixRun("C2", "llm_writeback", 2, "C2",
                       "C2_llm_writeback_minimal_s2") in rows
    assert M.MatrixRun("C2", "tcpg", 2, "C2_nota",
                       "C2_nota_tcpg_minimal_s2") in rows
    assert M.MatrixRun("C2", "llm_writeback", 4, "C2",
                       "C2_llm_writeback_minimal_s4") in rows
    assert M.MatrixRun("C2", "tcpg", 4, "C2_nota",
                       "C2_nota_tcpg_minimal_s4") in rows


def test_command_for_nota_adds_reproposal_flags():
    row = M.MatrixRun("C2", "tcpg", 1, "C2_nota",
                      "C2_nota_tcpg_minimal_s1")
    cmd = M.command_for(row)
    assert cmd[:4] == [sys.executable, "experiments/runner.py",
                       "--suite", "discovery"]
    assert ["--bias", "C2"] == cmd[4:6]
    assert "--nota-reproposal" in cmd
    assert cmd[cmd.index("--max-reproposal-rounds") + 1] == "2"


def test_command_for_baseline_has_case_name_but_no_nota_flags():
    row = M.MatrixRun("R5", "freedo_oracle", 4, "R5",
                      "R5_freedo_oracle_minimal_s4")
    cmd = M.command_for(row)
    assert "--case-name" in cmd
    assert cmd[cmd.index("--case-name") + 1] == "R5"
    assert "--nota-reproposal" not in cmd


def test_confound_combos_include_oracle_and_cases():
    rows = list(R.combos("confound", scripted=False))
    keys = {(r["case_name"], r["mode"]) for r in rows}
    assert ("F1", "tcpg") in keys
    assert ("F1", "freedo_oracle") in keys
    assert ("F1", "llm_writeback") in keys
    assert ("F3m", "freedo_oracle") in keys


def test_read_missing_csv_filters_completed(tmp_path, monkeypatch):
    runs = tmp_path / "runs" / "discovery"
    completed = runs / "R2_tcpg_minimal_s0"
    completed.mkdir(parents=True)
    completed.joinpath("summary.json").write_text("{}")
    monkeypatch.setattr(M, "RUNS_DIR", runs)
    path = tmp_path / "missing.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["repo_bias", "mode", "seed", "case_name", "run_id"],
        )
        w.writeheader()
        w.writerow({"repo_bias": "R2", "mode": "tcpg", "seed": 0,
                    "case_name": "R2", "run_id": "R2_tcpg_minimal_s0"})
        w.writerow({"repo_bias": "R2", "mode": "tcpg", "seed": 1,
                    "case_name": "R2", "run_id": "R2_tcpg_minimal_s1"})
    rows = M.read_missing_csv(path)
    assert [r.run_id for r in rows] == ["R2_tcpg_minimal_s1"]


def test_run_subprocess_times_out_process_group():
    status, output = M.run_subprocess(
        [sys.executable, "-u", "-c", "import time; print('start'); time.sleep(5)"],
        timeout_s=1,
    )
    assert status == "timeout"
    assert "start" in output


def test_r5_freedo_oracle_gt_candidate_coexists_with_vanilla_tool_prior():
    rt = TcpgRuntime(None, mode="freedo_oracle", execute_action=lambda a: True)
    vanilla = Candidate("gatherCoalOre", "capability", "held_tool",
                        "tier", ">=", "wooden", source="success_precondition")
    vanilla.status = "rejected"
    rt.cands[vanilla.cid] = vanilla

    oracle = R._freedo_oracle_gt_candidate("R5", "gatherCoalOre")
    assert oracle is not None
    oracle.status = "accepted"
    rt.cands[oracle.cid] = oracle

    assert rt.cands[vanilla.cid].status == "rejected"
    assert rt.cands[oracle.cid].target == "held_tool"
    assert rt.cands[oracle.cid].value == "stone"
    assert rt.cands[oracle.cid].status == "accepted"
