from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "mc_drift/tasks/u_tasks_final.yaml"
CONFIG_OUT = ROOT / "mc_drift/out/fabric_config/iap-drift/tasks.json"
MOD_ROOT = ROOT / "mc_drift/fabric-mod/iap-drift"


def test_build_fabric_config_static():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mc_drift.generator.build_fabric_config",
            "--tasks",
            str(TASKS),
            "--out",
            str(CONFIG_OUT),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    config = json.loads(CONFIG_OUT.read_text(encoding="utf-8"))
    enabled = {tid for tid, task in config["tasks"].items() if task["enabled"]}
    assert enabled == {"U17", "U19", "U21"}
    assert config["tasks"]["U17"]["target_blocks"] == ["minecraft:gold_ore", "minecraft:deepslate_gold_ore"]
    assert config["tasks"]["U19"]["ground_truth"] == "y_level(y) <= -10"
    assert config["tasks"]["U21"]["event"] == "block_break"
    assert config["tasks"]["U20"]["enabled"] is False


def test_fabric_sources_present_and_scoped():
    required = [
        "build.gradle",
        "gradle.properties",
        "src/main/resources/fabric.mod.json",
        "src/main/java/org/iap/mcdrift/IapDriftMod.java",
        "src/main/java/org/iap/mcdrift/config/DriftConfig.java",
        "src/main/java/org/iap/mcdrift/config/DriftTask.java",
        "src/main/java/org/iap/mcdrift/predicate/PredicateEvaluator.java",
        "src/main/java/org/iap/mcdrift/gate/MiningGate.java",
        "src/main/java/org/iap/mcdrift/logging/DriftLogger.java",
        "src/main/java/org/iap/mcdrift/command/IapDriftCommands.java",
    ]
    for rel in required:
        assert (MOD_ROOT / rel).exists(), rel

    mining_gate = (MOD_ROOT / "src/main/java/org/iap/mcdrift/gate/MiningGate.java").read_text(encoding="utf-8")
    assert "PlayerBlockBreakEvents.BEFORE" in mining_gate
    assert "return false;" in mining_gate
    assert "ground_truth" in mining_gate
    assert "serverPlayer.sendMessage" in mining_gate

    predicate = (MOD_ROOT / "src/main/java/org/iap/mcdrift/predicate/PredicateEvaluator.java").read_text(encoding="utf-8")
    assert "y_level\\\\(y\\\\)" in predicate or "y_level\\(y\\)" in predicate
    assert "unsupported predicate in Phase 3-4" in predicate
