"""Success-rate metrics, CSV I/O and aggregation into the paper tables."""
from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence, Tuple


def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion (no SciPy needed)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# Column schema for table_downstream.csv (one row per method x task x condition).
DOWNSTREAM_FIELDS = [
    "method",       # before | after | oracle | minus_nota | minus_boundary | ...
    "paper_id",     # paper task id, e.g. C1
    "task",         # repo task name, e.g. craftBoat
    "condition",    # origin | drift
    "k",            # successes
    "n",            # episodes (seeds)
    "success_rate",
    "ci_low",
    "ci_high",
    "mean_steps",
    "mean_replans",
]


@dataclass
class DownstreamRow:
    method: str
    paper_id: str
    task: str
    condition: str
    k: int
    n: int
    success_rate: float
    ci_low: float
    ci_high: float
    mean_steps: float
    mean_replans: float

    def as_dict(self) -> Dict:
        return asdict(self)


def write_downstream_csv(rows: Sequence[DownstreamRow], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DOWNSTREAM_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r.as_dict())


def read_downstream_csv(path: str) -> List[Dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Aggregation into the three paper tables that share this metric
# --------------------------------------------------------------------------- #
def to_table2(rows: Sequence[DownstreamRow]) -> List[Dict]:
    """Table 2 (overall planning performance): success per method x task x condition."""
    return [
        {
            "method": r.method,
            "paper_id": r.paper_id,
            "condition": r.condition,
            "success_rate": round(r.success_rate, 3),
            "ci_low": round(r.ci_low, 3),
            "ci_high": round(r.ci_high, 3),
            "n": r.n,
        }
        for r in rows
    ]


def to_table3_success(rows: Sequence[DownstreamRow], method: str = "after") -> List[Dict]:
    """Table 3 'writeback-then-success' column: the drift success rate of the
    written-back graph, per task."""
    return [
        {"paper_id": r.paper_id, "writeback_success": round(r.success_rate, 3), "n": r.n}
        for r in rows
        if r.method == method and r.condition == "drift"
    ]


def to_table4_success(rows: Sequence[DownstreamRow], ablations: Sequence[str]) -> List[Dict]:
    """Table 4 'success rate' column: drift success per ablation graph (mean
    over tasks)."""
    out: List[Dict] = []
    for method in ["after", *ablations]:
        drift = [r for r in rows if r.method == method and r.condition == "drift"]
        if not drift:
            continue
        k = sum(r.k for r in drift)
        n = sum(r.n for r in drift)
        rate = k / n if n else 0.0
        lo, hi = wilson_ci(k, n)
        out.append(
            {
                "variant": method,
                "success_rate": round(rate, 3),
                "ci_low": round(lo, 3),
                "ci_high": round(hi, 3),
                "n": n,
            }
        )
    return out
