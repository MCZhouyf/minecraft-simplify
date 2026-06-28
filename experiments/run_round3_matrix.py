"""Resumable round-3 main-matrix runner.

Runs the paper main matrix in small, restartable batches.  Completed runs are
detected by experiments/runs/discovery/<run_id>/summary.json and are never
overwritten.  Failures and timeouts are recorded, then the batch continues.

Typical use:
  python3 experiments/run_round3_matrix.py --env-file .experiment-env --limit 4
  python3 experiments/run_round3_matrix.py --refresh-missing
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "experiments" / "runs" / "discovery"
RESULTS_DIR = REPO / "experiments" / "results"
DEFAULT_MISSING = RESULTS_DIR / "round3_missing_main_matrix.csv"
FAILURES = RESULTS_DIR / "failures.md"
BACKUP_ROOT = Path.home() / "iap-results-backup"

RESOURCE_BIASES = ("R2", "R5", "R6")
CONTEXT_BIASES = ("C3", "C4", "C2")
MODES = ("tcpg", "freedo_oracle", "llm_writeback")


@dataclass(frozen=True)
class MatrixRun:
    repo_bias: str
    mode: str
    seed: int
    case_name: str
    run_id: str

    @property
    def done(self) -> bool:
        return (RUNS_DIR / self.run_id / "summary.json").exists()

    @property
    def is_nota(self) -> bool:
        return self.case_name.endswith("_nota")


def run_id_for(repo_bias: str, mode: str, seed: int, case_name: str) -> str:
    return f"{case_name}_{mode}_minimal_s{seed}"


def desired_matrix() -> List[MatrixRun]:
    rows: List[MatrixRun] = []
    for bias in RESOURCE_BIASES:
        for mode in MODES:
            for seed in range(5):
                case = bias
                rows.append(MatrixRun(bias, mode, seed, case,
                                      run_id_for(bias, mode, seed, case)))
        case = f"{bias}_nota"
        for seed in range(5):
            rows.append(MatrixRun(bias, "tcpg", seed, case,
                                  run_id_for(bias, "tcpg", seed, case)))
    for bias in CONTEXT_BIASES:
        for mode in MODES:
            for seed in range(5):
                case = bias
                rows.append(MatrixRun(bias, mode, seed, case,
                                      run_id_for(bias, mode, seed, case)))
        case = f"{bias}_nota"
        for seed in range(5):
            rows.append(MatrixRun(bias, "tcpg", seed, case,
                                  run_id_for(bias, "tcpg", seed, case)))
    return rows


def missing_matrix(rows: Iterable[MatrixRun] | None = None) -> List[MatrixRun]:
    return [r for r in (rows or desired_matrix()) if not r.done]


def write_missing_csv(path: Path = DEFAULT_MISSING) -> List[MatrixRun]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = missing_matrix()
    with path.open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["repo_bias", "mode", "seed", "case_name", "run_id"],
        )
        w.writeheader()
        for r in rows:
            w.writerow({
                "repo_bias": r.repo_bias,
                "mode": r.mode,
                "seed": r.seed,
                "case_name": r.case_name,
                "run_id": r.run_id,
            })
    return rows


def read_missing_csv(path: Path) -> List[MatrixRun]:
    rows: List[MatrixRun] = []
    if not path.exists():
        return write_missing_csv(path)
    with path.open(newline="") as f:
        for raw in csv.DictReader(f):
            rows.append(MatrixRun(
                raw["repo_bias"],
                raw["mode"],
                int(raw["seed"]),
                raw["case_name"],
                raw["run_id"],
            ))
    return [r for r in rows if not r.done]


def command_for(r: MatrixRun) -> List[str]:
    cmd = [
        sys.executable,
        "experiments/runner.py",
        "--suite", "discovery",
        "--bias", r.repo_bias,
        "--mode", r.mode,
        "--seed", str(r.seed),
        "--case-name", r.case_name,
        "--no-viewer",
    ]
    if r.is_nota:
        cmd.extend(["--nota-reproposal", "--max-reproposal-rounds", "2"])
    return cmd


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def append_failure(r: MatrixRun, cmd: List[str], status: str,
                   output: str = "") -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS_DIR / r.run_id
    k7_tail = ""
    k7 = run_dir / "k7.jsonl"
    if k7.exists():
        lines = k7.read_text(errors="ignore").splitlines()
        k7_tail = "\n".join(lines[-20:])
    episodes = ""
    ep = run_dir / "episodes.jsonl"
    if ep.exists():
        episodes = ep.read_text(errors="ignore")
    with FAILURES.open("a") as f:
        f.write(f"\n## {dt.datetime.utcnow().isoformat()}Z {r.run_id}\n\n")
        f.write(f"- status: `{status}`\n")
        f.write(f"- command: `{' '.join(cmd)}`\n\n")
        if output:
            f.write("### process tail\n\n```text\n")
            f.write(output[-8000:])
            f.write("\n```\n\n")
        if k7_tail:
            f.write("### k7 tail\n\n```jsonl\n")
            f.write(k7_tail)
            f.write("\n```\n\n")
        if episodes:
            f.write("### episodes\n\n```jsonl\n")
            f.write(episodes)
            f.write("\n```\n\n")


def run_subprocess(cmd: List[str], timeout_s: int) -> tuple[int | str, str]:
    p = subprocess.Popen(
        cmd,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = p.communicate(timeout=timeout_s)
        if output:
            print(output, end="")
        return p.returncode, output or ""
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if isinstance(output, bytes):
            output = output.decode(errors="ignore")
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            more, _ = p.communicate(timeout=5)
            output += more or ""
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            more, _ = p.communicate()
            output += more or ""
        if output:
            print(output, end="")
        return "timeout", output


def post_batch(batch_name: str, skip_audit: bool = False) -> None:
    subprocess.run([sys.executable, "experiments/evaluate.py"], cwd=REPO, check=False)
    subprocess.run([
        sys.executable, "experiments/cost_layering_report.py",
        "--runs", "experiments/runs",
        "--out", "experiments/results",
    ], cwd=REPO, check=False)
    if not skip_audit:
        subprocess.run([
            sys.executable, "experiments/audit_k7.py",
            "--runs", "experiments/runs",
        ], cwd=REPO, check=False)
    write_missing_csv()
    stamp = dt.datetime.utcnow().strftime("%m%d_%H%M%S")
    dst = BACKUP_ROOT / f"{batch_name}_{stamp}"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("runs", "results"):
        src = REPO / "experiments" / name
        if src.exists():
            shutil.copytree(src, dst / name, dirs_exist_ok=True)
    print(f"[backup] {dst}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--missing-csv", type=Path, default=DEFAULT_MISSING)
    ap.add_argument("--refresh-missing", action="store_true")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--timeout-s", type=int, default=1200)
    ap.add_argument("--bias", action="append", default=[],
                    help="filter repo bias; repeatable")
    ap.add_argument("--mode", action="append", default=[],
                    help="filter mode; repeatable")
    ap.add_argument("--case-name", action="append", default=[],
                    help="filter case_name; repeatable")
    ap.add_argument("--env-file", type=Path, default=None)
    ap.add_argument("--batch-name", default="round3_matrix_batch")
    ap.add_argument("--skip-post", action="store_true")
    ap.add_argument("--skip-audit", action="store_true")
    args = ap.parse_args(argv)

    if args.env_file:
        load_env_file(args.env_file)
    if args.refresh_missing:
        rows = write_missing_csv(args.missing_csv)
        print(f"[missing] {len(rows)} -> {args.missing_csv}")
        return 0

    rows = read_missing_csv(args.missing_csv)
    if args.bias:
        rows = [r for r in rows if r.repo_bias in set(args.bias)]
    if args.mode:
        rows = [r for r in rows if r.mode in set(args.mode)]
    if args.case_name:
        rows = [r for r in rows if r.case_name in set(args.case_name)]
    rows = rows[:max(args.limit, 0)]
    print(f"[batch] selected {len(rows)} run(s)")

    n_ok = n_fail = 0
    for r in rows:
        if r.done:
            print(f"[skip] {r.run_id}")
            continue
        cmd = command_for(r)
        print(f"[run] {r.run_id}: {' '.join(cmd)}")
        status, output = run_subprocess(cmd, args.timeout_s)
        if status == 0 and r.done:
            n_ok += 1
            print(f"[ok] {r.run_id}")
        else:
            n_fail += 1
            print(f"[fail] {r.run_id}: {status}")
            append_failure(r, cmd, str(status), output)

    if not args.skip_post:
        post_batch(args.batch_name, skip_audit=args.skip_audit)
    else:
        write_missing_csv(args.missing_csv)
    print(f"[done] ok={n_ok} failed={n_fail} missing={len(missing_matrix())}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
