"""Build Phase 0-2 datapack for MC-Drift.

Phase 1:
  Emit recipe overrides for resource_update tasks.

Phase 2:
  Emit block-tag override for U16 MineCoal, enforcing held_tool(tier) >= stone.

Usage:
  python -m mc_drift.generator.build_datapack \
      --tasks mc_drift/tasks/u_tasks_final.yaml \
      --labels mc_drift/tasks/u_tasks_labels.csv \
      --out mc_drift/out/datapacks \
      --pack-name iap_phase0_2
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required. Install with: pip install -r mc_drift/requirements-phase0-2.txt") from exc

from .predicate_parser import parse_predicate
from .recipe_registry import capability_tag_override_for, recipe_override_for


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("tasks YAML must contain a list at key 'tasks'.")
    return tasks


def _load_labels(path: Path | None) -> Dict[str, Dict[str, str]]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["任务编号"]: row for row in csv.DictReader(f)}


def _status_function(tasks: List[Dict[str, Any]], implemented: List[str]) -> str:
    return "\n".join([
        'tellraw @a {"text":"[IAP-Drift] Phase 0-2 datapack loaded.","color":"gold"}',
        f'tellraw @a {{"text":"[IAP-Drift] implemented tasks: {", ".join(implemented)}","color":"yellow"}}',
        ""
    ])


def build_datapack(
    tasks_path: Path,
    labels_path: Path | None,
    out_dir: Path,
    pack_name: str,
    pack_format: int = 10,
    clean: bool = True,
) -> Dict[str, Any]:
    tasks = _load_tasks(tasks_path)
    labels = _load_labels(labels_path)

    pack_dir = out_dir / pack_name
    if clean and pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)

    _write_json(pack_dir / "pack.mcmeta", {
        "pack": {
            "pack_format": pack_format,
            "description": "IAP MC-Drift Phase 0-2 controlled local mechanism drift"
        }
    })

    manifest: Dict[str, Any] = {
        "version": 1,
        "minecraft_version": "1.19.x",
        "pack_format": pack_format,
        "phase": "0-2",
        "source_tasks": str(tasks_path),
        "implemented_tasks": [],
        "unsupported_tasks": [],
        "notes": [
            "Phase 0 validates task/predicate manifests.",
            "Phase 1 implements resource_update via recipe overrides.",
            "Phase 2 implements U16 MineCoal held_tool(tier)>=stone via needs_stone_tool block tag.",
            "Situational, boundary, and held-item crafting gates require Fabric phases 3-6."
        ]
    }

    implemented_ids: List[str] = []

    for task in tasks:
        tid = task["id"]
        parsed = parse_predicate(task["ground_truth"])
        label_row = labels.get(tid, {})
        entry = {
            "id": tid,
            "action": task["action"],
            "goal": task["goal"],
            "family": task["family"],
            "ground_truth": task["ground_truth"],
            "predicate_type": parsed["type"],
            "semantic_label": label_row.get("标签"),
            "confidence": label_row.get("置信度"),
        }

        recipe = recipe_override_for(tid)
        if recipe:
            recipe_path = pack_dir / "data" / "minecraft" / "recipes" / f"{recipe['recipe_id']}.json"
            _write_json(recipe_path, recipe["json"])
            entry.update({
                "implemented": True,
                "injection": "recipe_override",
                "recipe_id": f"minecraft:{recipe['recipe_id']}",
                "path": str(recipe_path.relative_to(pack_dir)),
                "note": recipe.get("note", ""),
            })
            manifest["implemented_tasks"].append(entry)
            implemented_ids.append(tid)
            continue

        tag = capability_tag_override_for(tid)
        if tag:
            tag_path = pack_dir / tag["tag_path"]
            _write_json(tag_path, tag["json"])
            entry.update({
                "implemented": True,
                "injection": "block_tag_override",
                "path": tag["tag_path"],
                "note": tag.get("note", ""),
            })
            manifest["implemented_tasks"].append(entry)
            implemented_ids.append(tid)
            continue

        entry.update({
            "implemented": False,
            "injection": None,
            "reason": "Not implemented in Phase 0-2; requires Fabric runtime gate or later phase."
        })
        manifest["unsupported_tasks"].append(entry)

    _write_json(pack_dir / "data" / "iap_drift" / "manifest.json", manifest)
    (pack_dir / "data" / "iap_drift" / "functions").mkdir(parents=True, exist_ok=True)
    (pack_dir / "data" / "iap_drift" / "functions" / "status.mcfunction").write_text(
        _status_function(tasks, implemented_ids),
        encoding="utf-8"
    )

    summary = {
        "pack_dir": str(pack_dir),
        "implemented_count": len(manifest["implemented_tasks"]),
        "unsupported_count": len(manifest["unsupported_tasks"]),
        "implemented_by_injection": dict(Counter(e["injection"] for e in manifest["implemented_tasks"])),
    }
    _write_json(pack_dir / "data" / "iap_drift" / "phase0_2_summary.json", summary)
    return summary


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("mc_drift/out/datapacks"))
    parser.add_argument("--pack-name", default="iap_phase0_2")
    parser.add_argument("--pack-format", type=int, default=10)
    parser.add_argument("--no-clean", action="store_true")
    args = parser.parse_args(argv)

    summary = build_datapack(
        tasks_path=args.tasks,
        labels_path=args.labels,
        out_dir=args.out,
        pack_name=args.pack_name,
        pack_format=args.pack_format,
        clean=not args.no_clean,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
