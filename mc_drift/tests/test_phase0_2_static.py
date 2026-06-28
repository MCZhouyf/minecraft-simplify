from pathlib import Path
import json
import tempfile

from mc_drift.generator.validate_tasks import load_yaml, load_labels, validate_tasks
from mc_drift.generator.build_datapack import build_datapack


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "mc_drift/tasks/u_tasks_final.yaml"
LABELS = ROOT / "mc_drift/tasks/u_tasks_labels.csv"


def test_manifest_validation_ok():
    result = validate_tasks(load_yaml(TASKS), load_labels(LABELS))
    assert result["ok"], result
    assert result["task_count"] == 31
    assert result["family_counts"]["resource_update"] == 10
    assert result["family_counts"]["capability_update"] == 5
    assert result["family_counts"]["boundary_update"] == 7
    assert result["family_counts"]["situational_discovery"] == 9


def test_build_phase0_2_datapack_static():
    with tempfile.TemporaryDirectory() as tmp:
        summary = build_datapack(TASKS, LABELS, Path(tmp), "iap_phase0_2_test", clean=True)
        pack = Path(summary["pack_dir"])
        assert (pack / "pack.mcmeta").exists()
        assert (pack / "data/minecraft/recipes/oak_fence.json").exists()
        assert (pack / "data/minecraft/tags/blocks/needs_stone_tool.json").exists()

        manifest_path = pack / "data/iap_drift/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["implemented_tasks"]) == 11
        assert len(manifest["unsupported_tasks"]) == 20

        fence = json.loads((pack / "data/minecraft/recipes/oak_fence.json").read_text(encoding="utf-8"))
        assert fence["pattern"] == ["PPP", "PSP", "PPP"]
        assert fence["result"]["item"] == "minecraft:oak_fence"

        tag = json.loads((pack / "data/minecraft/tags/blocks/needs_stone_tool.json").read_text(encoding="utf-8"))
        assert "minecraft:coal_ore" in tag["values"]
        assert "minecraft:deepslate_coal_ore" in tag["values"]
