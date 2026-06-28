"""Build Fabric runtime config for MC-Drift Phase 3-4.

Phase 3:
  Fabric skeleton loads this JSON, exposes /iapdrift status|reload|dump,
  and writes truth JSONL logs.

Phase 4:
  Enables server-side block-break gates for mining y-level tasks:
    U17 MineGoldOre y_level(y) <= -14
    U19 MineDiamondOre y_level(y) <= -10
    U21 MineRedstone y_level(y) <= -12

Usage:
  python -m mc_drift.generator.build_fabric_config \
      --tasks mc_drift/tasks/u_tasks_final.yaml \
      --out mc_drift/out/fabric_config/iap-drift/tasks.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required. Install with: pip install -r mc_drift/requirements-phase3-4.txt") from exc


BLOCK_BREAK_TARGETS = {
    "U17": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore"],
    "U19": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
    "U21": ["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"],
}


def load_tasks(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("tasks YAML must contain a list at key 'tasks'")
    return tasks


def build_config(tasks_path: Path, out_path: Path) -> Dict[str, Any]:
    tasks = load_tasks(tasks_path)
    config: Dict[str, Any] = {
        "version": 1,
        "active": True,
        "phase": "3-4",
        "public_failure_message": "Action failed under current environment condition.",
        "truth_log_file": "iap_drift_logs/truth.jsonl",
        "tasks": {},
    }

    for task in tasks:
        tid = task["id"]
        phase_enabled = tid in BLOCK_BREAK_TARGETS
        event = "block_break" if phase_enabled else "not_implemented_phase_3_4"
        config["tasks"][tid] = {
            "id": tid,
            "enabled": phase_enabled,
            "action": task["action"],
            "goal": task["goal"],
            "family": task["family"],
            "event": event,
            "target_blocks": BLOCK_BREAK_TARGETS.get(tid, []),
            "ground_truth": task["ground_truth"],
            "origin": task["origin"],
            "drift": task["drift"],
            "public_failure_message": config["public_failure_message"],
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("mc_drift/out/fabric_config/iap-drift/tasks.json"))
    args = parser.parse_args()

    config = build_config(args.tasks, args.out)
    enabled = [tid for tid, task in config["tasks"].items() if task["enabled"]]
    print(json.dumps({
        "out": str(args.out),
        "task_count": len(config["tasks"]),
        "enabled_count": len(enabled),
        "enabled": enabled,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
