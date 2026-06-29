from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "mc_drift/tasks/u_tasks_round4.yaml"
CONFIG_OUT = ROOT / "mc_drift/out/fabric_config/iap-drift/tasks.json"
MOD_ROOT = ROOT / "mc_drift/fabric-mod/iap-drift"

def test_build_phase5_6_config():
    result = subprocess.run(
        [sys.executable, "-m", "mc_drift.generator.build_fabric_config_phase5_6",
         "--tasks", str(TASKS), "--out", str(CONFIG_OUT)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    cfg = json.loads(CONFIG_OUT.read_text(encoding="utf-8"))
    enabled = {tid for tid, task in cfg["tasks"].items() if task["enabled"]}
    assert {"U01", "U04", "U15", "U17", "U18", "U19", "U20", "U21", "U23"}.issubset(enabled)
    assert "U00" not in enabled
    assert cfg["tasks"]["U17"]["action"] == "mineGoldOre"
    assert cfg["tasks"]["U18"]["event"] == "smelting_output"
    assert cfg["tasks"]["U18"]["target_items"] == ["minecraft:iron_ingot"]
    assert cfg["tasks"]["U20"]["action"] == "smeltRawGold"
    assert cfg["tasks"]["U20"]["target_items"] == ["minecraft:gold_ingot"]

def test_java_sources_present():
    required = [
        "src/main/resources/iap-drift.mixins.json",
        "src/main/java/org/iap/mcdrift/predicate/PredicateEvaluator.java",
        "src/main/java/org/iap/mcdrift/gate/RuntimeGate.java",
        "src/main/java/org/iap/mcdrift/gate/GateDecision.java",
        "src/main/java/org/iap/mcdrift/mixin/CraftingResultSlotMixin.java",
        "src/main/java/org/iap/mcdrift/mixin/FurnaceOutputSlotMixin.java",
        "src/main/java/org/iap/mcdrift/command/IapDriftCommands.java",
    ]
    for rel in required:
        assert (MOD_ROOT / rel).exists(), rel
    predicate = (MOD_ROOT / "src/main/java/org/iap/mcdrift/predicate/PredicateEvaluator.java").read_text(encoding="utf-8")
    for token in ["NEARBY_BLOCK", "NEARBY_ENTITY", "TIME_WINDOW", "HELD_ITEM", "STATION_BASE_BLOCK"]:
        assert token in predicate
    assert 'literal("eval")' in (MOD_ROOT / "src/main/java/org/iap/mcdrift/command/IapDriftCommands.java").read_text(encoding="utf-8")
