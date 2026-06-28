"""Round-2 阶段1 bug 修复的离线测试：
(1) evaluator 正确给 llm_writeback 的无验证写回计分；
(2) 必要性方向确认的已知 E_in 边不被当作新发现的门控接受（修 F3m）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.ccg import CCG                              # noqa: E402
from Adam.tcpg.proposer import Candidate                  # noqa: E402


# ---------- 修复 2：已知 E_in 边不被误当新门控 ----------
def test_known_einput_edge_is_confirmed_not_accepted():
    g = CCG.init_default()
    # smeltRawIron 的 E_in 含 raw_iron / coal（配方输入）
    raw_iron = Candidate("smeltRawIron", "resource", "inventory_count",
                         "raw_iron", ">=", 1)
    assert g.is_known_input_edge(raw_iron)               # 是已知输入边
    g.write_back(raw_iron)
    # 不应作为新门控写入 e_ca
    assert "smeltRawIron" not in g.e_ca or \
        raw_iron.cid not in g.e_ca.get("smeltRawIron", [])
    # 但记录为 confirmed_known
    assert g.conditions[raw_iron.cid]["status"] == "confirmed_known"


def test_genuine_new_gate_still_written():
    g = CCG.init_default()
    # 真正的新门控（时间窗）不在 E_in 里，应正常写回为 accepted
    time_gate = Candidate("smeltRawIron", "environment", "time_of_day",
                          "time", "in", [0, 12000])
    assert not g.is_known_input_edge(time_gate)
    g.write_back(time_gate)
    assert time_gate.cid in g.e_ca["smeltRawIron"]
    assert g.conditions[time_gate.cid]["status"] == "accepted"


def test_f3m_scenario_no_false_confound_acceptance():
    """F3m 复现：raw_iron / coal 经必要性确认，但不应计为 confound 接受。"""
    g = CCG.init_default()
    for item in ("raw_iron", "coal"):
        c = Candidate("smeltRawIron", "resource", "inventory_count", item, ">=", 1)
        g.write_back(c)
    # e_ca 不应因这两个已知输入而新增门控
    assert not g.e_ca.get("smeltRawIron")
    statuses = {g.conditions[c]["status"] for c in g.conditions}
    assert statuses == {"confirmed_known"}


# ---------- 修复 1：evaluator 给 llm_writeback 计分 ----------
def _fake_llm_writeback_run(tmp, run_id, bias, accepted_conditions):
    d = tmp / "runs" / "discovery" / run_id
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(
        {"run_id": run_id, "suite": "discovery", "bias": bias,
         "mode": "llm_writeback", "feedback": "minimal", "seed": 0,
         "scripted": True, "episodes": 6, "steps_used": 0,
         "candidates": []}))           # llm_writeback summary 候选为空
    (d / "episodes.jsonl").write_text("\n".join(
        json.dumps({"episode": i, "natural_success": False, "steps_used": 0,
                    "decided": {}}) for i in range(6)))
    (d / "k7.jsonl").write_text("\n".join(
        json.dumps({"type": "writeback", "payload": {"cid": c["cid"]}})
        for c in accepted_conditions))
    # 写回的门控在 ccg.json 里
    conds = {c["cid"]: c for c in accepted_conditions}
    (d / "ccg.json").write_text(json.dumps(
        {"e_ca": {"mineGoldOre": list(conds)}, "conditions": conds,
         "e_in": {}, "e_out": {}, "rejected": {}}))


def test_evaluator_scores_llm_writeback_from_ccg(tmp_path, monkeypatch):
    import experiments.evaluate as EV
    monkeypatch.setattr(EV, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(EV, "OUT_DIR", tmp_path / "results")
    # R6 真因 held_tool>=diamond；llm_writeback 写回了 iron（vanilla 先验，错）
    # 和一个混杂 y_level，但都没写对真因
    gt_wrong = {"action": "mineGoldOre", "dimension": "capability",
                "target": "held_tool", "property": "pickaxe", "comparator": ">=",
                "value": "iron", "cid": "w1"}            # vanilla，非真因 diamond
    confound = {"action": "mineGoldOre", "dimension": "context",
                "target": "y_level", "property": "y", "comparator": "<=",
                "value": -16, "cid": "w2"}
    _fake_llm_writeback_run(tmp_path, "R6_llm_writeback_minimal_s0", "R6",
                            [gt_wrong, confound])
    EV.main([])
    rows = (tmp_path / "results" / "table4_discovery.csv").read_text().splitlines()
    rec = dict(zip(rows[0].split(","), rows[1].split(",")))
    assert rec["n_accepted"] == "2.0"                    # 修复前会是 0
    assert rec["gt_accepted"] == "0.0"                   # 没写对真因（只写了 vanilla iron）
    assert rec["confound_wrongly_accepted"] == "1.0"     # 错误接受了 y_level 混杂
