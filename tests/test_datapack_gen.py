"""Offline unit tests for mc_drift.datapack_gen (no Minecraft needed).

Round-3 suite (9 biases): R1 R2 R4 R5 R6 C1 C3 C4 C2.
  datapack_recipe: R2 (oak_fence 8-plank recipe)
  datapack_tag:    R5 (coal->stone tool), R6 (gold->diamond tool)
  mod_event:       R1 R2 R4 C1 C3 C4 C2
(craftReinforcedHandle=R3 deferred: skill unregistered.)
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc_drift import datapack_gen as dg  # noqa: E402

CFG = {"minecraft_version": "1.19.2", "minecraft_dir": "X", "world_name": "Y"}


def _gen(tmp_path, ids, cfg=None):
    return dg.generate(ids, out_dir=tmp_path, config=cfg or CFG)


def test_pack_format_mapping(tmp_path):
    # R5 is a datapack_tag bias -> produces a pack with pack.mcmeta
    for ver, pf in [("1.19", 10), ("1.19.2", 10), ("1.19.3", 10), ("1.19.4", 12)]:
        packs = _gen(tmp_path / ver, ["R5"], cfg={**CFG, "minecraft_version": ver})
        meta = json.loads((packs[0] / "pack.mcmeta").read_text())
        assert meta["pack"]["pack_format"] == pf


def test_per_bias_packs_for_tags(tmp_path):
    # R5/R6 are datapack_tag -> produce packs; R2 is now mod_event (craft gate),
    # so it is skipped by the datapack generator (only R5/R6 packs emitted).
    packs = _gen(tmp_path, ["R2", "R5", "R6"])
    names = sorted(p.name for p in packs)
    assert names == ["mc_drift_R5", "mc_drift_R6"]
    diamond = json.loads((tmp_path / "mc_drift_R6/data/minecraft/tags/blocks/needs_diamond_tool.json").read_text())
    assert diamond["replace"] is False
    assert set(diamond["values"]) == {"minecraft:gold_ore", "minecraft:deepslate_gold_ore"}
    for p_ in packs:
        meta = json.loads((p_ / "pack.mcmeta").read_text())
        assert meta["pack"]["pack_format"] == 10


def test_mod_event_biases_skipped_by_generator(tmp_path):
    # R1 is mod_event (no pack), R5 is datapack_tag (pack) -> only R5 pack emitted
    packs = _gen(tmp_path, ["R1", "R5"])
    assert [p.name for p in packs] == ["mc_drift_R5"]


def test_idempotent(tmp_path):
    def snap():
        return {str(f.relative_to(tmp_path)): f.read_bytes()
                for f in sorted(tmp_path.rglob("*")) if f.is_file()}
    _gen(tmp_path, ["all"]); s1 = snap()
    _gen(tmp_path, ["all"]); s2 = snap()
    # 2 datapack biases (R5 + R6 tags; R2 is now a mod craft gate, not a pack),
    # each pack = mcmeta + 1 data file
    assert s1 == s2 and len(s1) == 2 * 2


def test_unknown_bias_id_rejected(tmp_path):
    with pytest.raises(KeyError):
        _gen(tmp_path, ["Z9"])


def _write_biases(tmp_path, entry):
    doc = {"version": 1, "biases": [entry]}
    p = tmp_path / "biases.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True))
    return p


BASE = {
    "id": "R1", "level": "L1", "dimension": "resource", "action": "craftFurnace",
    "mechanism": "datapack_recipe",
    "payload": {"recipe_file": "furnace.json",
                "recipe_json": {"type": "minecraft:crafting_shaped",
                                "result": {"item": "minecraft:furnace"}}},
    "ground_truth": {"target": "inventory_count", "property": "coal",
                     "comparator": ">=", "value": 1},
    "failure_mode": "no_output",
    "solvability": {"verified": None, "oracle_plan_steps": None},
    "intervention_check": {"i_plus_compilable": None, "i_minus_compilable": None},
}


def test_schema_rejects_bad_mechanism(tmp_path):
    bad = dict(BASE, mechanism="datapck_recipe")
    with pytest.raises(ValueError, match="K1 schema"):
        dg.load_biases(_write_biases(tmp_path, bad), strict_actions=False)


def test_schema_rejects_bad_comparator(tmp_path):
    bad = dict(BASE, ground_truth=dict(BASE["ground_truth"], comparator="=="))
    with pytest.raises(ValueError, match="K1 schema"):
        dg.load_biases(_write_biases(tmp_path, bad), strict_actions=False)


def test_schema_rejects_tag_payload_on_recipe_mechanism(tmp_path):
    bad = dict(BASE, payload={"tag_file": "needs_iron_tool.json", "values_add": ["minecraft:x"]})
    with pytest.raises(ValueError, match="K1 schema"):
        dg.load_biases(_write_biases(tmp_path, bad), strict_actions=False)


def test_action_vocabulary_check(tmp_path):
    bad = dict(BASE, action="craftSpaceShuttle")
    if dg._known_action_names() is None:
        pytest.skip("Adam.util_info not importable in this environment")
    with pytest.raises(ValueError, match="vocabulary"):
        dg.load_biases(_write_biases(tmp_path, bad), strict_actions=True)


def test_shipped_biases_all_load_and_validate():
    biases = dg.load_biases(strict_actions=False)
    ids = [b["id"] for b in biases]
    assert ids == ["R1", "R2", "R4", "R5", "R6", "C1", "C3", "C4", "C2"]
    mech = {b["id"]: b["mechanism"] for b in biases}
    assert [k for k, v in mech.items() if v == "datapack_tag"] == ["R5", "R6"]
    # R2 moved datapack_recipe -> mod_event (craft_result gate); no datapack_recipe left
    assert [k for k, v in mech.items() if v == "datapack_recipe"] == []
    assert sum(v == "mod_event" for v in mech.values()) == 7


def test_export_mod_config_only_mod_event_entries(tmp_path):
    out = dg.export_mod_config(["all"], tmp_path / "mcdrift.json")
    cfg = json.loads(out.read_text())
    # tags (R5,R6) skipped; mod_event biases exported -- R2 is now a craft gate too
    assert cfg["enabled"] == ["R1", "R2", "R4", "C1", "C3", "C4", "C2"]
    assert cfg["gates"]["C4"]["gate"] == "block_break"
    assert cfg["gates"]["C4"]["params"]["block_match"] == "minecraft:(deepslate_)?diamond_ore"
    assert cfg["gates"]["C4"]["params"]["require"] == "player_y<="
    assert cfg["gates"]["C1"]["params"]["radius"] == 3
    assert cfg["gates"]["R1"]["params"]["require"] == "inventory_min"
    # R2: craft_result gate requiring >= 8 oak_planks on hand
    assert cfg["gates"]["R2"]["gate"] == "craft_result"
    assert cfg["gates"]["R2"]["params"]["require"] == "inventory_min"
    assert cfg["gates"]["R2"]["params"]["item"] == "minecraft:oak_planks"
    assert cfg["gates"]["R2"]["params"]["count"] == 8
    assert cfg["gates"]["C2"]["gate"] == "craft_result"
    assert cfg["gates"]["C2"]["params"]["require"] == "nearby_block"
    assert cfg["gates"]["C2"]["params"]["block"] == "minecraft:water"
    assert cfg["gates"]["C2"]["params"]["radius"] == 4
    assert cfg["gates"]["R1"]["params"]["count"] == 1
    assert cfg["feedback_text"]["C3"]["typed"] == "[environment]"


def test_export_mod_config_subset_and_empty(tmp_path):
    out = dg.export_mod_config(["C4", "C3"], tmp_path / "a.json")
    cfg = json.loads(out.read_text())
    assert cfg["enabled"] == ["C4", "C3"]
    out2 = dg.export_mod_config([], tmp_path / "b.json")
    cfg2 = json.loads(out2.read_text())
    assert cfg2["enabled"] == [] and cfg2["gates"] == {}
