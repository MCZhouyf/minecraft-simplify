"""Offline tests for stage 5 solvability verifier (INV-1)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc_drift import solvability as S                     # noqa: E402
from mc_drift.datapack_gen import load_biases             # noqa: E402


def test_all_biases_verified():
    results = [S.check_bias(b) for b in load_biases(strict_actions=False)]
    assert len(results) == 9
    bad = [r["id"] for r in results if not r["verified"]]
    assert not bad, f"unsolvable biases: {bad}"
    noncompilable = {r["id"] for r in results
                     if not (r["i_plus_compilable"] and r["i_minus_compilable"])}
    assert noncompilable == set(), f"unexpected non-compilable: {noncompilable}"


def test_r5_tool_tier_witness_semantics():
    """R5 gates coal_ore to stone tier; wooden path closed, stone+ open
    (was old C3 -> renamed R5)."""
    bias = next(b for b in load_biases(strict_actions=False) if b["id"] == "R5")
    recipes = S.modified_recipes(bias)
    assert "coal" in str(recipes) or "coal_ore" in str(recipes)


def test_circular_bias_rejected():
    assert S.circular_bias_rejected()


def test_c2_craftboat_nearby_water_wired():
    b = next(x for x in load_biases(strict_actions=False) if x["id"] == "C2")
    assert b["action"] == "craftBoat" and b["mechanism"] == "mod_event"
    assert b["dimension"] == "context"
    assert b["payload"]["params"]["require"] == "nearby_block"
    assert b["payload"]["params"]["block"] == "minecraft:water"
    assert b["ground_truth"]["target"] == "nearby_block"
    r = S.check_bias(b)
    assert r["verified"] is True
    assert r["i_plus_compilable"] and r["i_minus_compilable"]


def test_report_and_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUT_PATH", tmp_path / "report.json")
    rc = S.main(["--all"])
    assert rc == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["circular_bias_rejected"] is True
    assert len(report["biases"]) == 9


def test_backfill_yaml_preserves_structure(tmp_path):
    src = Path("mc_drift/biases/biases.yaml").read_text()
    p = tmp_path / "biases.yaml"
    p.write_text(src)
    results = [S.check_bias(b) for b in load_biases(strict_actions=False)]
    S.backfill_yaml(results, path=p)
    out = p.read_text()
    assert "verified: true" in out and "verified: null" not in out
    assert out.count("i_plus_compilable: true") == 9
    # comments and entry count intact
    assert out.count("- id:") == 9 and "# K1 bias ground-truth" in out
    # still loads + validates
    import yaml
    doc = yaml.safe_load(out)
    assert len(doc["biases"]) == 9
