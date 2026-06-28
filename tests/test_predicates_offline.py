"""Offline tests for stage 3: schema sanity, K1xSigma cross-check, client
validation, and the signature-bootstrap derive/report pipeline."""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import predicates as P                      # noqa: E402
from Adam.tcpg import signature_bootstrap as SB            # noqa: E402
from mc_drift.datapack_gen import load_biases              # noqa: E402

FIXTURE = json.loads((REPO / "tests/fixtures/state_snapshot_example.json").read_text())


# ----------------------------------------------------------- schema sanity
def test_schema_structure():
    s = P.schema()
    assert set(s["whitelist"]) == {"resource", "capability", "procedure",
                                   "context", "environment"}
    for dim, targets in s["whitelist"].items():
        for t in targets:
            assert t in s["primitives"], f"{dim} whitelists unknown primitive {t}"
    assert "weather" in s["observe_only"]


def test_every_bias_ground_truth_is_expressible():
    """K1 x Sigma_MC consistency: each bias ground truth lies in the whitelist
    of its own dimension with an allowed comparator (paper INV-3 + C1)."""
    for b in load_biases(strict_actions=False):
        gt, dim = b["ground_truth"], b["dimension"]
        assert P.in_whitelist(dim, gt["target"]), (
            f"{b['id']}: target {gt['target']} not whitelisted for {dim}")
        ok, why = P.validate_predicate({"id": b["id"], **gt})
        assert ok, f"{b['id']}: {why}"


# ----------------------------------------------------------- client validation
@pytest.mark.parametrize("bad,frag", [
    ({"id": "x", "target": "mana_level", "property": "y", "comparator": ">=", "value": 1},
     "unknown target"),
    ({"id": "x", "target": "held_tool", "property": "tier", "comparator": "in", "value": "iron"},
     "comparator"),
    ({"id": "x", "target": "held_tool", "property": "tier", "comparator": ">=", "value": "obsidian"},
     "not a tier"),
    ({"id": "x", "target": "time_of_day", "property": "time", "comparator": "in", "value": 6000},
     "range"),
    ({"id": "x", "target": "y_level", "property": "y", "comparator": "<=", "value": None},
     "missing field"),
])
def test_validate_predicate_rejects(bad, frag):
    ok, why = P.validate_predicate(bad)
    assert not ok and frag in why


# ----------------------------------------------------------- bootstrap pipeline
def test_flatten_types():
    fields = SB.flatten_types(FIXTURE)
    assert fields["agent.y"] == "number"
    assert fields["world.is_raining"] == "bool"
    assert fields["held.name"] == "string"
    assert fields["inventory"] == "map"


def test_derive_and_residue(tmp_path):
    draft = SB.derive_signature(SB.flatten_types(FIXTURE))
    grounded = {v["grounds_core_primitive"] for v in draft["derived"].values()
                if v["grounds_core_primitive"]}
    assert {"y_level", "time_of_day", "held_tool", "held_item",
            "inventory_count", "block_below", "sky_exposed", "weather"} <= grounded
    stats = SB.residue_report(draft, out_md=tmp_path / "residue.md")
    assert stats["core_primitives"] == 12
    assert stats["auto_derived"] >= 8
    assert set(stats["manual_list"]) == {"nearby_block", "station_type",
                                         "station_base_block", "ingredient_type"}
    md = (tmp_path / "residue.md").read_text()
    assert "auto-derived" in md and str(stats["manual_residue_lines"]) in md
