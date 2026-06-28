from mc_drift.generator.generate_drift_tasks import generate
from mc_drift.generator.filter import check_bias
from pathlib import Path


def test_static_generation_smoke(tmp_path: Path):
    report = generate(
        raw=80,
        final_specs=12,
        seeds_per_spec=2,
        seed=123,
        out_dir=tmp_path,
        families=["resource_update", "capability_update", "boundary_update", "situational_discovery"],
        runtime_check=False,
    )
    assert report["selected_specs"] == 12
    assert report["task_seed_pairs"] == 24
    assert (tmp_path / "generated_biases.yaml").exists()
    assert (tmp_path / "generated_tasks.yaml").exists()
    assert (tmp_path / "generated_task_pairs.txt").exists()


def test_capability_self_lock_is_rejected():
    bias = {
        "id": "TMP",
        "level": "L2",
        "dimension": "capability",
        "action": "mineGoldOre",
        "mechanism": "datapack_tag",
        "payload": {
            "tag_file": "needs_diamond_tool.json",
            "values_add": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore"],
        },
        "ground_truth": {
            "target": "held_tool",
            "property": "tier",
            "comparator": ">=",
            "value": "diamond",
        },
        "failure_mode": "no_output",
        "feedback_text": {"typed": "[capability]", "hinted": "Mining now requires a diamond-tier pickaxe or better."},
        "authored_blind": False,
        "solvability": {"verified": None, "oracle_plan_steps": None},
        "intervention_check": {"i_plus_compilable": None, "i_minus_compilable": None},
    }
    result = check_bias(bias, runtime_check=True)
    assert not result.passed
    assert "bootstrap_tool_unreachable:self_lock" in result.reasons


def test_capability_requires_unstaged_witness_is_rejected():
    bias = {
        "id": "TMP",
        "level": "L2",
        "dimension": "capability",
        "action": "mineIronOre",
        "mechanism": "datapack_tag",
        "payload": {
            "tag_file": "needs_iron_tool.json",
            "values_add": ["minecraft:deepslate_iron_ore"],
        },
        "ground_truth": {
            "target": "held_tool",
            "property": "tier",
            "comparator": ">=",
            "value": "iron",
        },
        "failure_mode": "no_output",
        "feedback_text": {"typed": "[capability]", "hinted": "Mining now requires an iron-tier pickaxe or better."},
        "authored_blind": False,
        "solvability": {"verified": None, "oracle_plan_steps": None},
        "intervention_check": {"i_plus_compilable": None, "i_minus_compilable": None},
    }
    result = check_bias(bias, runtime_check=True)
    assert not result.passed
    assert "bootstrap_requires_unstaged_witness" in result.reasons
