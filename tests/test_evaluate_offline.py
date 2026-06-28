"""Offline test: evaluate.py aggregation over a synthetic discovery run."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def fake_run(tmp, run_id, bias, mode, cands, episodes, k7):
    d = tmp / "runs" / "discovery" / run_id
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(
        {"run_id": run_id, "suite": "discovery", "bias": bias, "mode": mode,
         "feedback": "minimal", "seed": 0, "scripted": True, "episodes": 6,
         "steps_used": 17, "candidates": cands}))
    (d / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes))
    (d / "k7.jsonl").write_text("\n".join(json.dumps(e) for e in k7))


def test_discovery_aggregation(tmp_path, monkeypatch, capsys):
    import experiments.evaluate as EV
    monkeypatch.setattr(EV, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(EV, "OUT_DIR", tmp_path / "results")
    gt = {"action": "mineGoldOre", "dimension": "capability", "target": "held_tool",
          "property": "pickaxe", "comparator": ">=", "value": "diamond",
          "cid": "x", "status": "accepted", "source": "tcpg", "origin": "core"}
    conf = dict(gt, target="y_level", property="y", comparator="<=", value=-16,
                status="rejected", cid="y")
    eps = [{"episode": 0, "natural_success": False, "steps_used": 9,
            "decided": {}},
           {"episode": 1, "natural_success": False, "steps_used": 17,
            "decided": {"held_tool": "accepted"}}]
    k7 = ([{"type": "proposal", "payload": {}}] +
          [{"type": "intervention_start", "payload": {}}] * 4 +
          [{"type": "retry", "payload": {}}] * 4 +
          [{"type": "undo", "payload": {"ok": True, "ctx_match": True}}] * 3 +
          [{"type": "undo", "payload": {"ok": True, "ctx_match": False}}])
    fake_run(tmp_path, "R6_tcpg_minimal_s0", "R6", "tcpg", [gt, conf], eps, k7)
    EV.main([])
    rows = (tmp_path / "results" / "table4_discovery.csv").read_text().splitlines()
    assert len(rows) == 2
    hdr, val = rows[0].split(","), rows[1].split(",")
    rec = dict(zip(hdr, val))
    assert rec["bias"] == "R6" and rec["precision"] == "1.0"
    assert rec["gt_accepted"] == "1.0" and rec["confound_rejected"] == "1.0"
    assert rec["voided_obs"] == "1.0" and rec["interventions"] == "4.0"
    assert rec["episodes_to_decision"] in ("2", "2.0")
    curve = (tmp_path / "results" / "lifelong_curve.csv").read_text()
    assert "tcpg,0,0.0" in curve and "tcpg,1,0.0" in curve


def test_discovery_ignores_diagnostic_runs_but_keeps_nota(tmp_path, monkeypatch):
    import experiments.evaluate as EV
    monkeypatch.setattr(EV, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(EV, "OUT_DIR", tmp_path / "results")

    gt = {"action": "craftFence", "dimension": "resource", "target": "inventory_count",
          "property": "oak_planks", "comparator": ">=", "value": 8,
          "cid": "x", "status": "accepted", "source": "frontier", "origin": "core"}
    eps = [{"episode": 0, "natural_success": False, "steps_used": 12,
            "decided": {"inventory_count": "accepted"}}]
    k7 = [{"type": "writeback", "payload": {"decision": "accepted"}}]

    fake_run(tmp_path, "R2_nota_tcpg_minimal_s0", "R2", "tcpg", [gt], eps, k7)
    nota_summary = json.loads((tmp_path / "runs" / "discovery" /
                               "R2_nota_tcpg_minimal_s0" / "summary.json").read_text())
    nota_summary["nota_reproposal"] = True
    nota_summary["config_overrides"] = {"nota_reproposal": True, "max_reproposal_rounds": 2}
    (tmp_path / "runs" / "discovery" /
     "R2_nota_tcpg_minimal_s0" / "summary.json").write_text(json.dumps(nota_summary))

    fake_run(tmp_path, "R2_diagnostics", "R2", "tcpg", [gt], eps, k7)

    EV.main([])
    assert not (tmp_path / "results" / "table4_discovery.csv").exists()

    table6 = (tmp_path / "results" / "table6_ablation.csv").read_text()
    assert "tcpg_nota" in table6
