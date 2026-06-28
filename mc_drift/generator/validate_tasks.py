"""Validate MC-Drift task YAML and optional label CSV.

Usage:
  python -m mc_drift.generator.validate_tasks mc_drift/tasks/u_tasks_final.yaml \
      --labels mc_drift/tasks/u_tasks_labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required. Install with: pip install -r mc_drift/requirements-phase0-2.txt") from exc

from .predicate_parser import PredicateParseError, parse_predicate

ALLOWED_FAMILIES = {
    "resource_update",
    "capability_update",
    "boundary_update",
    "situational_discovery",
}

REQUIRED_TASK_FIELDS = ["id", "action", "goal", "family", "ground_truth", "origin", "drift"]


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Top-level YAML must be a mapping.")
    return data


def load_labels(path: Path) -> Dict[str, Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    labels = {}
    for row in rows:
        tid = row.get("任务编号") or row.get("id")
        if not tid:
            raise ValueError(f"Label row without task id: {row}")
        if tid in labels:
            raise ValueError(f"Duplicate label row: {tid}")
        labels[tid] = row
    return labels


def validate_tasks(data: Dict[str, Any], labels: Dict[str, Dict[str, str]] | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    tasks = data.get("tasks")

    if data.get("version") != 1:
        errors.append(f"Expected version: 1, got {data.get('version')!r}")

    if not isinstance(tasks, list):
        errors.append("'tasks' must be a list.")
        tasks = []

    ids = []
    family_counts = Counter()
    predicate_counts = Counter()

    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"Task at index {i} is not a mapping.")
            continue

        for field in REQUIRED_TASK_FIELDS:
            if field not in task:
                errors.append(f"Task index {i} missing required field: {field}")

        tid = task.get("id")
        if not isinstance(tid, str) or not tid.startswith("U") or len(tid) != 3:
            errors.append(f"Task index {i} has invalid id: {tid!r}")
        else:
            ids.append(tid)
            expected = f"U{i:02d}"
            if tid != expected:
                warnings.append(f"Task id {tid} at index {i}; expected consecutive id {expected}.")

        family = task.get("family")
        if family not in ALLOWED_FAMILIES:
            errors.append(f"{tid}: unsupported family {family!r}")
        else:
            family_counts[family] += 1

        gt = task.get("ground_truth")
        if isinstance(gt, str):
            try:
                parsed = parse_predicate(gt)
                predicate_counts[parsed["type"]] += 1
            except PredicateParseError as exc:
                errors.append(f"{tid}: {exc}")
        else:
            errors.append(f"{tid}: ground_truth must be string.")

        if labels is not None and tid not in labels:
            warnings.append(f"{tid}: no semantic label row found.")

    duplicate_ids = [tid for tid, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        errors.append(f"Duplicate ids: {duplicate_ids}")

    if labels is not None:
        task_ids = set(ids)
        extra = sorted(set(labels) - task_ids)
        if extra:
            warnings.append(f"Label rows without tasks: {extra}")

    return {
        "ok": not errors,
        "task_count": len(tasks),
        "family_counts": dict(family_counts),
        "predicate_counts": dict(predicate_counts),
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tasks", type=Path)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    data = load_yaml(args.tasks)
    labels = load_labels(args.labels) if args.labels else None
    result = validate_tasks(data, labels)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"MC-Drift validation: {'OK' if result['ok'] else 'FAILED'}")
        print(f"tasks: {result['task_count']}")
        print(f"families: {result['family_counts']}")
        print(f"predicates: {result['predicate_counts']}")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}", file=sys.stderr)
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
