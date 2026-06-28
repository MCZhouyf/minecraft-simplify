"""Generate randomized MC-Drift biases and origin/drift task-pair files.

Example:
  python -m mc_drift.generator.generate_drift_tasks \
    --raw 1000 \
    --final-specs 60 \
    --seeds-per-spec 5 \
    --seed 7 \
    --out-dir mc_drift/out/generated

Outputs:
  generated_biases.yaml       K1-compatible standalone bias file
  generated_tasks.yaml        machine-readable origin/drift task pairs
  generated_task_pairs.txt    human-readable origin/drift task pairs
  generation_report.json      raw/valid/final counts and filter reasons

Then, to use the generated bias file with the existing datapack generator:
  python -m mc_drift.generator.install_generated \
    --bias-file mc_drift/out/generated/generated_biases.yaml \
    --generate \
    --export-mod-config
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

import yaml

from .filter import apply_result_to_bias, check_bias
from .templates import DriftSpec, SAMPLERS, assign_k1_ids, sample_spec, spec_key


DEFAULT_FAMILIES = ["resource_update", "capability_update", "boundary_update", "situational_discovery"]


def _balanced_select(specs: List[DriftSpec], final_specs: int, families: Sequence[str]) -> List[DriftSpec]:
    by_family: Dict[str, List[DriftSpec]] = defaultdict(list)
    seen = set()
    for s in specs:
        k = spec_key(s)
        if k in seen:
            continue
        seen.add(k)
        by_family[s.family].append(s)

    base_quota = final_specs // len(families)
    remainder = final_specs % len(families)
    selected: List[DriftSpec] = []
    for i, fam in enumerate(families):
        quota = base_quota + (1 if i < remainder else 0)
        fam_specs = by_family.get(fam, [])
        if len(fam_specs) < quota:
            raise ValueError(f"insufficient valid specs for family={fam}: need {quota}, have {len(fam_specs)}")
        selected.extend(fam_specs[:quota])

    # Fill any shortage from remaining valid specs without duplicating.
    if len(selected) < final_specs:
        selected_keys = {spec_key(s) for s in selected}
        for s in specs:
            if spec_key(s) not in selected_keys:
                selected.append(s)
                selected_keys.add(spec_key(s))
            if len(selected) >= final_specs:
                break

    return selected[:final_specs]


def _write_biases_yaml(specs: List[DriftSpec], path: Path) -> None:
    doc = {"version": 1, "biases": [s.bias for s in specs]}
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _build_tasks(specs: List[DriftSpec], seeds_per_spec: int) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    for s in specs:
        bid = s.bias["id"]
        for seed in range(seeds_per_spec):
            tasks.append({
                "id": f"{bid}_seed{seed}",
                "bias_id": bid,
                "seed": seed,
                "family": s.family,
                "template": s.template,
                "action": s.action,
                "origin": s.origin_task,
                "drift": s.drift_task,
                "ground_truth": s.bias["ground_truth"],
                "distractors": s.distractors,
            })
    return tasks


def _write_tasks_yaml(tasks: List[Dict[str, Any]], path: Path) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "tasks": tasks}, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _write_task_pairs_txt(tasks: List[Dict[str, Any]], path: Path) -> None:
    lines: List[str] = []
    for t in tasks:
        gt = t["ground_truth"]
        if isinstance(gt["value"], list):
            v = "[" + ", ".join(map(str, gt["value"])) + "]"
        else:
            v = str(gt["value"])
        lines.append(f"[{t['id']}] bias={t['bias_id']} family={t['family']} action={t['action']} seed={t['seed']}")
        lines.append(f"origin: {t['origin']}")
        lines.append(f"drift: {t['drift']}")
        lines.append(f"ground_truth: {gt['target']}({gt['property']}) {gt['comparator']} {v}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def generate(
    *,
    raw: int,
    final_specs: int,
    seeds_per_spec: int,
    seed: int,
    out_dir: Path,
    families: Sequence[str],
    runtime_check: bool,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_specs: List[DriftSpec] = []
    valid_specs: List[DriftSpec] = []
    failures: Counter[str] = Counter()
    filter_records: List[Dict[str, Any]] = []

    for _ in range(raw):
        spec = sample_spec(rng, families)
        raw_specs.append(spec)
        result = check_bias(spec.bias, runtime_check=runtime_check)
        filter_records.append({
            "family": spec.family,
            "template": spec.template,
            "action": spec.action,
            "ground_truth": spec.bias["ground_truth"],
            "filter": result.to_dict(),
        })
        if result.passed:
            b = apply_result_to_bias(spec.bias, result)
            valid_specs.append(DriftSpec(spec.family, spec.template, spec.action, b, spec.origin_task, spec.drift_task, spec.predicate_text, spec.distractors))
        else:
            failures.update(result.reasons)

    # De-duplicate before balancing.
    dedup: List[DriftSpec] = []
    seen = set()
    for s in valid_specs:
        k = spec_key(s)
        if k not in seen:
            dedup.append(s)
            seen.add(k)

    selected = _balanced_select(dedup, final_specs, families)
    selected = assign_k1_ids(selected)

    tasks = _build_tasks(selected, seeds_per_spec)

    _write_biases_yaml(selected, out_dir / "generated_biases.yaml")
    _write_tasks_yaml(tasks, out_dir / "generated_tasks.yaml")
    _write_task_pairs_txt(tasks, out_dir / "generated_task_pairs.txt")

    report = {
        "seed": seed,
        "runtime_check": runtime_check,
        "raw_attempts": raw,
        "raw_family_counts": dict(Counter(s.family for s in raw_specs)),
        "valid_unique_specs": len(dedup),
        "valid_family_counts": dict(Counter(s.family for s in dedup)),
        "selected_specs": len(selected),
        "selected_family_counts": dict(Counter(s.family for s in selected)),
        "task_seed_pairs": len(tasks),
        "filter_failure_counts": dict(failures),
        "outputs": {
            "biases": str(out_dir / "generated_biases.yaml"),
            "tasks": str(out_dir / "generated_tasks.yaml"),
            "task_pairs_txt": str(out_dir / "generated_task_pairs.txt"),
        },
        "filter_records": filter_records[:200],  # keep report compact
    }
    (out_dir / "generation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=int, default=1000, help="number of candidate drift specs to sample before filtering")
    ap.add_argument("--final-specs", type=int, default=60, help="number of unique drift specs to keep")
    ap.add_argument("--seeds-per-spec", type=int, default=5, help="number of task seeds per unique spec")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=Path("mc_drift/out/generated"))
    ap.add_argument("--families", default=",".join(DEFAULT_FAMILIES), help="comma-separated family names")
    ap.add_argument("--no-runtime-check", action="store_true", help="skip Adam.tcpg compiler checks; use static checks only")
    args = ap.parse_args(argv)

    families = [x.strip() for x in args.families.split(",") if x.strip()]
    unknown = sorted(set(families) - set(SAMPLERS))
    if unknown:
        raise SystemExit(f"unknown families: {unknown}; choices={sorted(SAMPLERS)}")
    report = generate(
        raw=args.raw,
        final_specs=args.final_specs,
        seeds_per_spec=args.seeds_per_spec,
        seed=args.seed,
        out_dir=args.out_dir,
        families=families,
        runtime_check=not args.no_runtime_check,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "filter_records"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
