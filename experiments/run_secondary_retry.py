"""Run secondary suites one cell at a time with 504-aware retries.

This is intentionally scoped to secondary tables only:
  * confound Table 7, including tcpg_nota rows
  * feedback_ladder Table 3

Existing clean runs are skipped. Existing runs whose k7 contains a 504 are
archived and retried. A retry that produces another 504 is archived, then the
script waits before trying again.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "experiments" / "runs"
sys.path.insert(0, str(ROOT))

from experiments.runner import TASKS, make_env, run_one  # noqa: E402


@dataclass(frozen=True)
class Job:
    suite: str
    run_id: str
    bias: str
    mode: str
    seed: int = 0
    feedback: str = "minimal"
    case_name: Optional[str] = None
    spot_override: Optional[str] = None
    inventory_extra: Optional[Dict[str, Any]] = None
    commands_extra: tuple = ()
    inventory_remove: tuple = ()
    cfg_overrides: Optional[Dict[str, Any]] = None


def _run_dir(job: Job) -> Path:
    return RUNS / job.suite / job.run_id


def _has_504(run_dir: Path) -> bool:
    k7 = run_dir / "k7.jsonl"
    return k7.exists() and "504" in k7.read_text(errors="ignore")


def _is_clean_complete(job: Job) -> bool:
    d = _run_dir(job)
    return (d / "summary.json").exists() and not _has_504(d)


def _archive(run_dir: Path, archive_root: Path, why: str) -> None:
    if not run_dir.exists():
        return
    dst = archive_root / f"{run_dir.name}_{why}_{int(time.time())}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir), str(dst))
    print(f"archived {run_dir} -> {dst}", flush=True)


def _secondary_jobs() -> list[Job]:
    tcpg_secondary = {"step_budget": 80, "max_interventions_per_event": 6}
    jobs: list[Job] = []

    conf = TASKS["suites"]["confound"]["cases"]
    for case, spec in conf.items():
        for mode in ("tcpg", "freedo_oracle", "llm_writeback"):
            jobs.append(Job(
                suite="confound",
                run_id=f"{case}_{mode}_minimal_s0",
                bias=spec["bias"],
                mode=mode,
                case_name=case,
                spot_override=spec.get("spot_override"),
                inventory_extra=spec.get("inventory_extra"),
                commands_extra=tuple(spec.get("commands_extra", ())),
                inventory_remove=tuple(spec.get("inventory_remove", ())),
                cfg_overrides=tcpg_secondary if mode == "tcpg" else None,
            ))
        jobs.append(Job(
            suite="confound",
            run_id=f"nota_{case}_tcpg_minimal_s0",
            bias=spec["bias"],
            mode="tcpg",
            case_name=f"nota_{case}",
            spot_override=spec.get("spot_override"),
            inventory_extra=spec.get("inventory_extra"),
            commands_extra=tuple(spec.get("commands_extra", ())),
            inventory_remove=tuple(spec.get("inventory_remove", ())),
            cfg_overrides={
                **tcpg_secondary,
                "nota_reproposal": True,
                "max_reproposal_rounds": 2,
            },
        ))

    for bias in TASKS["suites"]["feedback_ladder"]["biases"]:
        for feedback in TASKS["suites"]["feedback_ladder"]["feedback"]:
            jobs.append(Job(
                suite="feedback_ladder",
                run_id=f"{bias}_tcpg_{feedback}_s0",
                bias=bias,
                mode="tcpg",
                feedback=feedback,
                cfg_overrides=tcpg_secondary,
            ))
    return jobs


def _filter_jobs(jobs: Iterable[Job], pattern: Optional[str]) -> list[Job]:
    if not pattern:
        return list(jobs)
    return [j for j in jobs if pattern in j.run_id or pattern in j.suite]


def _kill_stale_mineflayer() -> None:
    """Only kill exact node bridge commands; avoid broad patterns that hit us."""
    out = subprocess.run(
        ["pgrep", "-f", "node /root/iap-agent/env/mineflayer/index.js"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout
    for line in out.splitlines():
        pid = line.strip()
        if pid:
            subprocess.run(["kill", pid], check=False)


def _pick_model(models: list[str], attempt: int) -> Optional[str]:
    if not models:
        return None
    return models[(attempt - 1) % len(models)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-seconds", type=int, default=180,
                    help="delay after a 504/bad attempt before retrying")
    ap.add_argument("--max-attempts", type=int, default=5)
    ap.add_argument("--job", default=None,
                    help="substring filter, e.g. nota_F3 or feedback_ladder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-stale-bots", action="store_true")
    ap.add_argument(
        "--models",
        default="",
        help="comma-separated fallback model list used per retry attempt",
    )
    args = ap.parse_args()

    jobs = _filter_jobs(_secondary_jobs(), args.job)
    archive_root = RUNS / "_archive" / f"secondary_retry_{time.strftime('%m%d_%H%M%S')}"

    pending = [j for j in jobs if not _is_clean_complete(j)]
    print(f"secondary jobs: {len(jobs)}; pending/dirty: {len(pending)}", flush=True)
    for j in pending:
        d = _run_dir(j)
        state = "missing"
        if (d / "summary.json").exists():
            state = "dirty_504" if _has_504(d) else "complete"
        elif d.exists():
            state = "incomplete"
        print(f"  {j.run_id}: {state}", flush=True)
    if args.dry_run:
        return 0

    if not args.keep_stale_bots:
        _kill_stale_mineflayer()

    env = make_env(viewer_port=-1)
    model_cycle = [m.strip() for m in args.models.split(",") if m.strip()]
    for job in pending:
        if _is_clean_complete(job):
            print(f"skip clean {job.run_id}", flush=True)
            continue
        d = _run_dir(job)
        if d.exists():
            _archive(d, archive_root, "stale")
        for attempt in range(1, args.max_attempts + 1):
            model = _pick_model(model_cycle, attempt)
            if model:
                os.environ["IAP_LLM_MODEL"] = model
            print(
                f"RUN {job.run_id} attempt {attempt}/{args.max_attempts}"
                + (f" model={model}" if model else ""),
                flush=True,
            )
            try:
                run_one(
                    env,
                    job.suite,
                    job.bias,
                    job.mode,
                    job.seed,
                    feedback=job.feedback,
                    spot_override=job.spot_override,
                    inventory_extra=job.inventory_extra,
                    commands_extra=job.commands_extra,
                    inventory_remove=job.inventory_remove,
                    case_name=job.case_name,
                    cfg_overrides=job.cfg_overrides,
                )
            except Exception as exc:  # keep the batch moving; evidence is in logs.
                print(f"ERROR {job.run_id}: {exc!r}", flush=True)
            d = _run_dir(job)
            if (d / "summary.json").exists() and not _has_504(d):
                print(f"OK {job.run_id}", flush=True)
                break
            why = "504" if _has_504(d) else "bad"
            _archive(d, archive_root, why)
            if attempt < args.max_attempts:
                print(f"waiting {args.wait_seconds}s before retry", flush=True)
                time.sleep(args.wait_seconds)
        else:
            print(f"FAILED_AFTER_RETRIES {job.run_id}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
