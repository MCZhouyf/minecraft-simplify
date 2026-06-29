
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any, Dict, List
import yaml

BLOCK_BREAK_TARGETS = {
    "U17": ["minecraft:gold_ore", "minecraft:deepslate_gold_ore"],
    "U19": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
    "U21": ["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"],
}
SMELTING_OUTPUT_TASKS = {"U18", "U20"}

def mcid(x: str) -> str:
    return x if x.startswith("minecraft:") else f"minecraft:{x}"

def load_tasks(path: Path) -> List[Dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["tasks"]

def infer_event(t: Dict[str, Any]) -> str:
    if t["id"] in BLOCK_BREAK_TARGETS: return "block_break"
    if t["id"] in SMELTING_OUTPUT_TASKS or t["action"].startswith("smelt"): return "smelting_output"
    if t["action"].startswith("craft"): return "crafting_output"
    return "not_implemented_phase_5_6"

def build_config(tasks_path: Path, out_path: Path) -> Dict[str, Any]:
    config = {
        "version": 2,
        "active": True,
        "phase": "5-6",
        "public_failure_message": "Action failed under current environment condition.",
        "truth_log_file": "iap_drift_logs/truth.jsonl",
        "tasks": {},
    }
    for t in load_tasks(tasks_path):
        event = infer_event(t)
        enabled = event in {"block_break", "crafting_output", "smelting_output"} and t["family"] != "resource_update"
        target_item = t.get("target_item") or t["goal"]
        config["tasks"][t["id"]] = {
            "id": t["id"],
            "enabled": enabled,
            "action": t["action"],
            "goal": t["goal"],
            "target_item": target_item,
            "family": t["family"],
            "event": event,
            "target_blocks": BLOCK_BREAK_TARGETS.get(t["id"], []),
            "target_items": [mcid(target_item)] if event in {"crafting_output", "smelting_output"} else [],
            "ground_truth": t["ground_truth"],
            "origin": t["origin"],
            "drift": t["drift"],
            "public_failure_message": config["public_failure_message"],
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("mc_drift/out/fabric_config/iap-drift/tasks.json"))
    args = ap.parse_args()
    cfg = build_config(args.tasks, args.out)
    enabled = [k for k, v in cfg["tasks"].items() if v["enabled"]]
    by_event = {}
    for k in enabled:
        ev = cfg["tasks"][k]["event"]
        by_event[ev] = by_event.get(ev, 0) + 1
    print(json.dumps({"out": str(args.out), "task_count": len(cfg["tasks"]), "enabled_count": len(enabled), "enabled": enabled, "enabled_by_event": by_event}, indent=2, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
