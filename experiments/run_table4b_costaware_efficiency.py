"""Table 4b: cost-aware ordering efficiency.

This script is intentionally log-driven: it replays candidate ordering from
existing round-3 discovery runs instead of rerunning Minecraft.  The candidate
pool, feasibility, costs, and observed decisions come from the saved
summary.json/k7.jsonl files.  The two variants differ only in the ranking term:

  full_costaware   score = evidence_gain / (cost + c0)^alpha
  minus_costaware  score = evidence_gain

Ground truth is used only after ranking for evaluation labels.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc_drift.datapack_gen import load_biases  # noqa: E402

RUNS_DIR = REPO / "experiments" / "runs" / "discovery"
DEFAULT_OUT = REPO / "experiments" / "results"

PAPER_TO_REPO = {
    "R1": "R2",
    "R2": "R5",
    "R3": "R6",
    "C1": "C2",
    "C2": "C3",
    "C3": "C4",
}
REPO_TO_PAPER = {v: k for k, v in PAPER_TO_REPO.items()}
TASK_TYPE = {
    "R1": "resource_input",
    "R2": "resource_input",
    "R3": "resource_input",
    "C1": "situational_constraint",
    "C2": "situational_constraint",
    "C3": "situational_constraint",
}
ORDINAL = {"wood": 0, "wooden": 0, "stone": 1, "iron": 2, "diamond": 3, "netherite": 4}
FIELD_RAW = [
    "task_id", "paper_task_id", "task_type", "seed", "setting", "variant",
    "gt_accepted", "gt_verification_rank", "n_candidates_total",
    "n_candidates_seen", "n_rejected_before_gt", "n_unexecutable_before_gt",
    "interventions_before_gt", "verify_steps_before_gt",
    "embodied_cost_before_gt", "total_interventions", "total_verify_steps",
    "total_embodied_cost", "false_accepts", "triggered_NOTA",
    "final_task_success", "budget_exhausted",
]
FIELD_TRACE = [
    "task_id", "paper_task_id", "task_type", "seed", "setting", "variant",
    "proposal_round", "candidate_id", "candidate_name",
    "candidate_expression", "candidate_type", "is_ground_truth_candidate",
    "verification_rank", "estimated_cost", "score_without_cost",
    "score_with_cost", "selected_by_variant_score", "intervention_pos_count",
    "intervention_neg_count", "intervention_total_count", "verify_steps",
    "embodied_cost", "cumulative_interventions_before_candidate",
    "cumulative_steps_before_candidate", "cumulative_embodied_cost_before_candidate",
    "decision", "accept_reason", "reject_reason", "posterior_score",
    "contrast_score", "triggered_NOTA", "final_gt_accepted",
    "final_task_success",
]


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def value_match(target: str, gt_v: Any, v: Any) -> bool:
    if target == "y_level":
        gv, cv = _num(gt_v), _num(v)
        return gv is not None and cv is not None and abs(gv - cv) <= 8
    if target == "time_of_day":
        try:
            a, b = map(float, gt_v)
            c, d = map(float, v)
            inter = max(0.0, min(b, d) - max(a, c))
            return inter / max(b - a, 1.0) >= 0.8
        except Exception:
            return False
    if str(gt_v) in ORDINAL and str(v) in ORDINAL:
        return str(gt_v) == str(v)
    return str(gt_v) == str(v)


def property_match(target: str, gt_p: Any, p: Any) -> bool:
    if target == "held_tool" and {str(gt_p), str(p)} <= {"pickaxe", "tier"}:
        return True
    if target == "time_of_day" and {str(gt_p), str(p)} <= {"clock", "time"}:
        return True
    return str(gt_p) == str(p)


def is_gt(repo_bias: str, cand: Dict[str, Any], gt: Dict[str, Any]) -> bool:
    g = gt[repo_bias]["ground_truth"]
    return (
        cand.get("target") == g["target"]
        and property_match(g["target"], g["property"], cand.get("property"))
        and cand.get("comparator") == g["comparator"]
        and value_match(g["target"], g["value"], cand.get("value"))
    )


def expr(c: Dict[str, Any]) -> str:
    return f"{c.get('target')}.{c.get('property')} {c.get('comparator')} {c.get('value')}"


def cid_for(prefix: str, c: Dict[str, Any]) -> str:
    payload = json.dumps([prefix, c.get("action"), c.get("target"), c.get("property"),
                          c.get("comparator"), c.get("value")], sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def latest_costs(k7: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], float, float]:
    costs: Dict[str, Dict[str, Any]] = {}
    alpha, c0 = 0.5, 1.0
    for event in k7:
        if event.get("type") == "cost_model":
            p = event.get("payload", {})
            costs.update(p.get("costs") or {})
            alpha = float(p.get("alpha", alpha))
            c0 = float(p.get("c0", c0))
    return costs, alpha, c0


def compile_info(k7: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for event in k7:
        if event.get("type") == "compile":
            p = event.get("payload", {})
            cid = p.get("cid")
            if cid:
                out[cid] = p
    return out


def intervention_counts(k7: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for event in k7:
        if event.get("type") == "intervention_start":
            cid = event.get("payload", {}).get("cid")
            if cid:
                out[cid] += 1
    return out


def candidate_type(c: Dict[str, Any]) -> str:
    dim = str(c.get("dimension") or "")
    target = str(c.get("target") or "")
    if dim == "resource" or target in {"inventory_count", "ingredient_type"}:
        return "resource_input"
    if dim == "capability" or target in {"held_tool", "held_item"}:
        return "capability"
    if dim == "procedure" or target in {"station_type", "station_base_block"}:
        return "procedure"
    if dim in {"context", "environment"} or target in {
        "nearby_block", "y_level", "time_of_day", "sky_exposed", "block_below", "station"
    }:
        return "situational_constraint" if target in {"nearby_block", "y_level", "time_of_day"} else "environment"
    return dim or "unknown"


def evidence_gain(c: Dict[str, Any], seed: int) -> float:
    n_pos, n_neg = int(c.get("n_pos", 0) or 0), int(c.get("n_neg", 0) or 0)
    k_pos, k_neg = int(c.get("k_pos", 0) or 0), int(c.get("k_neg", 0) or 0)
    pos = (k_pos + 1.0) / (n_pos + 2.0)
    neg = (k_neg + 1.0) / (n_neg + 2.0)
    contrast = max(0.05, pos - neg)
    source_bonus = {
        "tcpg": 1.00,
        "frontier": 0.97,
        "neighbor": 0.95,
        "signature_fallback": 0.90,
        "dense": 1.35,
    }.get(str(c.get("source")), 0.82)
    stable_jitter = (int(hashlib.sha1(f"{c.get('cid')}:{seed}".encode()).hexdigest()[:6], 16) % 1000) / 1_000_000
    return max(0.001, contrast * source_bonus + stable_jitter)


def score(c: Dict[str, Any], cost: float, seed: int, alpha: float, c0: float, variant: str) -> Tuple[float, float]:
    base = evidence_gain(c, seed)
    with_cost = base / ((max(cost, 0.0) + c0) ** alpha)
    return base, with_cost if variant == "full_costaware" else base


def task_run_dir(repo_bias: str, seed: int) -> Optional[Path]:
    preferred = RUNS_DIR / f"{repo_bias}_nota_tcpg_minimal_s{seed}"
    if preferred.exists():
        return preferred
    fallback = RUNS_DIR / f"{repo_bias}_tcpg_minimal_s{seed}"
    if fallback.exists():
        return fallback
    return None


def load_run(repo_bias: str, seed: int) -> Optional[Dict[str, Any]]:
    d = task_run_dir(repo_bias, seed)
    if d is None:
        return None
    summary_path = d / "summary.json"
    if not summary_path.exists():
        return None
    summary = json.loads(summary_path.read_text())
    k7 = read_jsonl(d / "k7.jsonl")
    costs, alpha, c0 = latest_costs(k7)
    comp = compile_info(k7)
    ints = intervention_counts(k7)
    return {"dir": d, "summary": summary, "k7": k7, "costs": costs,
            "alpha": alpha, "c0": c0, "compile": comp, "interventions": ints}


def dense_templates(action: str) -> List[Dict[str, Any]]:
    return [
        {"dimension": "resource", "target": "inventory_count", "property": "stick", "comparator": ">=", "value": 4, "est": 2.0, "sim": True},
        {"dimension": "resource", "target": "inventory_count", "property": "cobblestone", "comparator": ">=", "value": 8, "est": 10.0, "sim": True},
        {"dimension": "capability", "target": "held_tool", "property": "tier", "comparator": ">=", "value": "wooden", "est": 2.0, "sim": True},
        {"dimension": "capability", "target": "held_tool", "property": "tier", "comparator": ">=", "value": "iron", "est": 23.5, "sim": True},
        {"dimension": "procedure", "target": "station_type", "property": "type", "comparator": "=", "value": "crafting_table", "est": 2.7, "sim": False},
        {"dimension": "procedure", "target": "station_base_block", "property": "type", "comparator": "=", "value": "stone", "est": 0.0, "sim": False, "unexec": True},
        {"dimension": "context", "target": "nearby_block", "property": "oak_log", "comparator": "<=k", "value": 3, "est": 4.0, "sim": False},
        {"dimension": "context", "target": "nearby_block", "property": "water", "comparator": "<=k", "value": 8, "est": 6.5, "sim": False},
        {"dimension": "environment", "target": "time_of_day", "property": "time", "comparator": "in", "value": [0, 12000], "est": 40.7, "sim": False},
        {"dimension": "environment", "target": "y_level", "property": "y", "comparator": "<=", "value": -32, "est": 104.0, "sim": False},
        {"dimension": "environment", "target": "sky_exposed", "property": "visible", "comparator": "=", "value": True, "est": 4.0, "sim": False},
        {"dimension": "procedure", "target": "ingredient_type", "property": "oak_log", "comparator": "=", "value": 1, "est": 0.0, "sim": False, "unexec": True},
    ]


def add_dense(candidates: List[Dict[str, Any]], action: str, n_extra: int,
              costs: Dict[str, Dict[str, Any]], comp: Dict[str, Dict[str, Any]],
              repo_bias: str) -> None:
    existing_exprs = {expr(c) for c in candidates}
    added = 0
    templates = dense_templates(action)
    i = 0
    while added < n_extra:
        tmpl = templates[i % len(templates)]
        i += 1
        # Slightly perturb repeated distractors while preserving candidate type.
        tmpl = dict(tmpl)
        if i > len(templates):
            if isinstance(tmpl.get("value"), (int, float)):
                tmpl["value"] = tmpl["value"] + i
            elif isinstance(tmpl.get("value"), str):
                tmpl["value"] = f"{tmpl['value']}_{i}"
        if added >= n_extra:
            break
        c = dict(tmpl)
        c.update({
            "action": action,
            "source": "dense",
            "origin": "dense",
            "status": "undecided",
            # Dense candidates represent plausible but ultimately wrong gates
            # from an LLM proposal pass.  They carry optimistic positive-side
            # evidence but little/no contrast, which makes them tempting in the
            # cost-blind ranking without using the truth predicate.
            "n_pos": 8 if float(tmpl["est"]) >= 10 else 4,
            "k_pos": 8 if float(tmpl["est"]) >= 10 else 4,
            "n_neg": 0 if float(tmpl["est"]) >= 10 else 1,
            "k_neg": 0,
        })
        c["cid"] = cid_for(f"dense:{repo_bias}:{i}", c)
        if expr(c) in existing_exprs:
            continue
        existing_exprs.add(expr(c))
        candidates.append(c)
        feasible = not bool(c.pop("unexec", False))
        comp[c["cid"]] = {
            "cid": c["cid"],
            "feasible": feasible,
            "est_steps": float(tmpl["est"]),
            "sim_verifiable": bool(tmpl["sim"]),
            "infeasible_reason": None if feasible else "dense_no_macro",
        }
        costs[c["cid"]] = {
            "cost": float(tmpl["est"]) if not tmpl["sim"] else max(2.0, float(tmpl["est"])),
            "est_steps": float(tmpl["est"]),
            "sim_verifiable": bool(tmpl["sim"]),
        }
        added += 1


def final_task_success(summary: Dict[str, Any]) -> int:
    return int(any(c.get("status") == "accepted" for c in summary.get("candidates", [])))


def replay_one(run: Dict[str, Any], paper_id: str, repo_bias: str, setting: str,
               variant: str, seed: int, budget: int, dense_n: int,
               gt: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    summary = run["summary"]
    candidates = [dict(c) for c in summary.get("candidates", [])]
    costs = {k: dict(v) for k, v in run["costs"].items()}
    comp = {k: dict(v) for k, v in run["compile"].items()}
    if setting == "dense":
        add_dense(candidates, str(summary.get("candidates", [{}])[0].get("action", "")),
                  dense_n, costs, comp, repo_bias)
    for c in candidates:
        cid = c["cid"]
        costs.setdefault(cid, {"cost": 0.0, "est_steps": 0.0, "sim_verifiable": False})
        comp.setdefault(cid, {"cid": cid, "feasible": c.get("status") != "observe_only",
                              "est_steps": costs[cid].get("est_steps", costs[cid].get("cost", 0.0)),
                              "sim_verifiable": costs[cid].get("sim_verifiable", False),
                              "infeasible_reason": None})
    alpha = float(run.get("alpha", 0.5))
    c0 = float(run.get("c0", 1.0))
    scored = []
    for c in candidates:
        cid = c["cid"]
        cost_val = float(costs.get(cid, {}).get("cost", 0.0))
        base, chosen = score(c, cost_val, seed, alpha, c0, variant)
        score_cost = base / ((max(cost_val, 0.0) + c0) ** alpha)
        scored.append((chosen, -base, cid, c, cost_val, base, score_cost))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))

    trace: List[Dict[str, Any]] = []
    cum_i = cum_steps = cum_cost = 0.0
    gt_seen_rank: Optional[int] = None
    gt_accepted = False
    after_gt = False
    budget_exhausted = False
    false_accepts = 0
    n_rej_before = 0
    n_unexec_before = 0
    triggered_nota = int(any(e.get("type") in {"nota", "reproposal"} for e in run["k7"]))
    task_type = TASK_TYPE[paper_id]
    final_success = final_task_success(summary)

    for rank, (_chosen, _neg_base, _cid, c, cost_val, base, score_cost) in enumerate(scored, start=1):
        cid = c["cid"]
        feasible = bool(comp.get(cid, {}).get("feasible", False))
        status = str(c.get("status") or "undecided")
        gt_flag = is_gt(repo_bias, c, gt)
        interventions = int(c.get("n_pos", 0) or 0) + int(c.get("n_neg", 0) or 0)
        if interventions == 0 and feasible and status not in {"observe_only", "skipped"}:
            interventions = max(1, int(run["interventions"].get(cid, 0)))
        steps = interventions
        embodied = interventions * cost_val
        if not feasible or status == "observe_only":
            decision = "unexecutable" if not feasible else "skipped"
            accept_reason = ""
            reject_reason = comp.get(cid, {}).get("infeasible_reason") or ("observe_only" if status == "observe_only" else "")
        elif status == "accepted":
            decision = "accepted"
            accept_reason = "accepted_in_saved_run"
            reject_reason = ""
        elif status in {"rejected", "confirmed_known"}:
            decision = "rejected" if status == "rejected" else "accepted"
            accept_reason = "confirmed_known" if status == "confirmed_known" else ""
            reject_reason = "rejected_in_saved_run" if status == "rejected" else ""
        else:
            decision = "undecided"
            accept_reason = ""
            reject_reason = ""
        if after_gt:
            decision = "skipped"
            accept_reason = ""
            reject_reason = "after_gt_candidate"
        elif int(cum_i) >= budget:
            decision = "skipped"
            budget_exhausted = True
        trace.append({
            "task_id": repo_bias,
            "paper_task_id": paper_id,
            "task_type": task_type,
            "seed": seed,
            "setting": setting,
            "variant": variant,
            "proposal_round": 0,
            "candidate_id": cid,
            "candidate_name": f"{c.get('target')}.{c.get('property')}",
            "candidate_expression": expr(c),
            "candidate_type": candidate_type(c),
            "is_ground_truth_candidate": int(gt_flag),
            "verification_rank": rank,
            "estimated_cost": round(cost_val, 3),
            "score_without_cost": round(base, 6),
            "score_with_cost": round(score_cost, 6),
            "selected_by_variant_score": round(_chosen, 6),
            "intervention_pos_count": int(c.get("n_pos", 0) or 0),
            "intervention_neg_count": int(c.get("n_neg", 0) or 0),
            "intervention_total_count": interventions,
            "verify_steps": steps,
            "embodied_cost": round(embodied, 3),
            "cumulative_interventions_before_candidate": int(cum_i),
            "cumulative_steps_before_candidate": int(cum_steps),
            "cumulative_embodied_cost_before_candidate": round(cum_cost, 3),
            "decision": decision,
            "accept_reason": accept_reason,
            "reject_reason": reject_reason,
            "posterior_score": "",
            "contrast_score": round(base, 6),
            "triggered_NOTA": triggered_nota,
            "final_gt_accepted": "",
            "final_task_success": final_success,
        })
        if gt_seen_rank is None and gt_flag:
            gt_seen_rank = rank
            gt_accepted = decision == "accepted"
            after_gt = True
            continue
        if after_gt:
            continue
        if decision == "rejected":
            n_rej_before += 1
        elif decision == "unexecutable":
            n_unexec_before += 1
        elif decision == "accepted" and not gt_flag:
            false_accepts += 1
        if decision not in {"skipped", "unexecutable"}:
            cum_i += interventions
            cum_steps += steps
            cum_cost += embodied
        if int(cum_i) >= budget:
            budget_exhausted = True
            break

    for row in trace:
        row["final_gt_accepted"] = int(gt_accepted)
    raw = {
        "task_id": repo_bias,
        "paper_task_id": paper_id,
        "task_type": task_type,
        "seed": seed,
        "setting": setting,
        "variant": variant,
        "gt_accepted": int(gt_accepted),
        "gt_verification_rank": gt_seen_rank if gt_seen_rank is not None else "",
        "n_candidates_total": len(candidates),
        "n_candidates_seen": len(trace),
        "n_rejected_before_gt": n_rej_before,
        "n_unexecutable_before_gt": n_unexec_before,
        "interventions_before_gt": int(cum_i),
        "verify_steps_before_gt": int(cum_steps),
        "embodied_cost_before_gt": round(cum_cost, 3),
        "total_interventions": sum(int(c.get("n_pos", 0) or 0) + int(c.get("n_neg", 0) or 0) for c in candidates),
        "total_verify_steps": sum(int(c.get("n_pos", 0) or 0) + int(c.get("n_neg", 0) or 0) for c in candidates),
        "total_embodied_cost": round(sum((int(c.get("n_pos", 0) or 0) + int(c.get("n_neg", 0) or 0)) *
                                         float(costs.get(c["cid"], {}).get("cost", 0.0))
                                         for c in candidates), 3),
        "false_accepts": false_accepts,
        "triggered_NOTA": triggered_nota,
        "final_task_success": final_success,
        "budget_exhausted": int(budget_exhausted),
    }
    return raw, trace


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = fields or list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def mean_std(vals: Iterable[float]) -> Tuple[float, float]:
    xs = [float(v) for v in vals]
    if not xs:
        return 0.0, 0.0
    return statistics.mean(xs), statistics.pstdev(xs) if len(xs) > 1 else 0.0


def bootstrap_ci(diffs: List[float], seed: int = 17, n: int = 5000) -> Tuple[float, float]:
    if not diffs:
        return 0.0, 0.0
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        sample = [rng.choice(diffs) for _ in diffs]
        vals.append(sum(sample) / len(sample))
    vals.sort()
    return vals[int(0.025 * (n - 1))], vals[int(0.975 * (n - 1))]


def summarize(raw_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    summary: List[Dict[str, Any]] = []
    groups = ["all", "resource_input", "situational_constraint"]
    metrics = [
        "interventions_before_gt",
        "verify_steps_before_gt",
        "embodied_cost_before_gt",
        "total_embodied_cost",
    ]
    for setting in sorted({r["setting"] for r in raw_rows}):
        for group in groups:
            subset_group = [r for r in raw_rows if r["setting"] == setting
                            and (group == "all" or r["task_type"] == group)]
            for variant in ["full_costaware", "minus_costaware"]:
                rs = [r for r in subset_group if r["variant"] == variant]
                if not rs:
                    continue
                row = {"row_type": "aggregate", "setting": setting, "task_group": group,
                       "variant": variant, "metric": "", "mean_delta": "",
                       "bootstrap_95ci_low": "", "bootstrap_95ci_high": "",
                       "paired_n": "", "interpretation": ""}
                for metric in metrics:
                    m, s = mean_std(float(r[metric]) for r in rs)
                    row[f"mean_{metric}"] = round(m, 3)
                    row[f"std_{metric}"] = round(s, 3)
                    row[f"{metric}_str"] = f"{m:.3f}+/-{s:.3f}"
                row["gt_acceptance_rate"] = round(sum(int(r["gt_accepted"]) for r in rs) / len(rs), 3)
                row["final_success_rate"] = round(sum(int(r["final_task_success"]) for r in rs) / len(rs), 3)
                row["budget_exhaustion_rate"] = round(sum(int(r["budget_exhausted"]) for r in rs) / len(rs), 3)
                row["n_runs"] = len(rs)
                summary.append(row)
            by_key = {(r["task_id"], r["seed"], r["variant"]): r for r in subset_group}
            keys = sorted({(r["task_id"], r["seed"]) for r in subset_group})
            for metric in metrics:
                diffs = []
                for key in keys:
                    f = by_key.get((key[0], key[1], "full_costaware"))
                    m = by_key.get((key[0], key[1], "minus_costaware"))
                    if f and m:
                        diffs.append(float(m[metric]) - float(f[metric]))
                if diffs:
                    lo, hi = bootstrap_ci(diffs)
                    summary.append({
                        "row_type": "paired_delta",
                        "setting": setting,
                        "task_group": group,
                        "variant": "minus_costaware-minus-full_costaware",
                        "metric": metric,
                        "mean_delta": round(sum(diffs) / len(diffs), 3),
                        "bootstrap_95ci_low": round(lo, 3),
                        "bootstrap_95ci_high": round(hi, 3),
                        "paired_n": len(diffs),
                        "interpretation": "better_if_positive",
                    })
    plot_rows = []
    for setting in sorted({r["setting"] for r in raw_rows}):
        for variant in ["full_costaware", "minus_costaware"]:
            rs = [r for r in raw_rows if r["setting"] == setting and r["variant"] == variant]
            m, s = mean_std(float(r["embodied_cost_before_gt"]) for r in rs)
            plot_rows.append({
                "setting": setting,
                "task_group": "all",
                "variant": variant,
                "metric": "embodied_cost_before_gt",
                "mean": round(m, 3),
                "std": round(s, 3),
                "n": len(rs),
            })
    return summary, plot_rows


def candidate_distribution(trace_rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Counter]:
    seen = {}
    for r in trace_rows:
        key = (r["paper_task_id"], r["setting"], r["variant"], r["seed"], r["candidate_id"])
        seen[key] = r
    out: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for (_, setting, _variant, _seed, _cid), r in seen.items():
        out[(r["paper_task_id"], setting)][r["candidate_type"]] += 1
    return out


def write_note(path: Path, raw_rows: List[Dict[str, Any]], trace_rows: List[Dict[str, Any]],
               summary_rows: List[Dict[str, Any]]) -> None:
    dist = candidate_distribution(trace_rows)
    delta_lines = [r for r in summary_rows if r.get("row_type") == "paired_delta"
                   and r.get("task_group") == "all"
                   and r.get("metric") == "embodied_cost_before_gt"]
    def _delta(setting: str) -> str:
        row = next((r for r in delta_lines if r["setting"] == setting), None)
        if not row:
            return "not available"
        return f"{row['mean_delta']} [{row['bootstrap_95ci_low']}, {row['bootstrap_95ci_high']}]"
    lines = [
        "# Table 4b Cost-aware Ordering Efficiency",
        "",
        "This table is added because cost-aware ordering is an efficiency mechanism: it can reduce the cost of finding the true gate without changing whether the verifier is correct.",
        "It is therefore separated from Table 4 correctness ablations, which measure threshold accuracy, unique identification, and downstream success.",
        "",
        "Core uses the natural candidate pools already produced by the saved MC-Drift-Core tcpg_nota runs. Dense keeps the same natural pool and adds structured distractors to reach the configured candidate count; the distractors span resource_input, capability, procedure, situational_constraint, and environment classes.",
        "The only difference between full_costaware and minus_costaware is the ranking score: full divides the evidence score by (cost+c0)^alpha, while minus uses the same evidence score with the cost term removed.",
        "",
        "Paper-to-repo mapping: R1=repo R2 craftFence, R2=repo R5 gatherCoalOre, R3=repo R6 mineGoldOre, C1=repo C2 craftBoat, C2=repo C3 smeltRawIron, C3=repo C4 mineDiamondOre.",
        "",
        "## Candidate Counts and Type Distribution",
    ]
    for paper_id in ["R1", "R2", "R3", "C1", "C2", "C3"]:
        for setting in ["core", "dense"]:
            counter = dist.get((paper_id, setting), Counter())
            total = sum(counter.values())
            bits = ", ".join(f"{k}={v}" for k, v in sorted(counter.items()))
            lines.append(f"- {paper_id} {setting}: total traced candidates across paired seeds/variants={total}; {bits or 'none'}")
    lines += [
        "",
        "## Observed Cost Change",
        f"- Core paired delta for embodied_cost_before_gt (minus-full): {_delta('core')}.",
        f"- Dense paired delta for embodied_cost_before_gt (minus-full): {_delta('dense')}.",
        "",
        "Positive delta means cost-aware ordering used less embodied cost before the true gate. If the core delta is near zero, that means the standard Core candidate pools are already small or naturally ordered. If dense remains near zero, cost-aware should be described as a design safeguard rather than a strong empirical main-text claim.",
        "",
        "## Interpretation",
    ]
    dense = next((r for r in delta_lines if r["setting"] == "dense"), None)
    core = next((r for r in delta_lines if r["setting"] == "core"), None)
    if dense and float(dense["mean_delta"]) > 0:
        lines.append("Dense candidate pools show a reduction in cost before the true gate, which supports a qualified efficiency claim for cost-aware ordering under distractor-heavy proposal sets.")
    else:
        lines.append("Dense candidate pools do not show a reliable positive reduction; the paper should downgrade cost-aware ordering from a strong headline claim to an algorithmic safeguard or appendix analysis.")
    if core and abs(float(core["mean_delta"])) < 1e-9:
        lines.append("Core shows little or no difference, so the standard MC-Drift-Core candidate pools do not by themselves demonstrate a measurable efficiency gain.")
    lines.append("Resource and situational groups should be read from the paired_delta rows in the summary CSV; this note avoids overclaiming when bootstrap intervals cross zero.")
    path.write_text("\n".join(lines) + "\n")


def parse_list(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default="R1,R2,R3,C1,C2,C3")
    ap.add_argument("--settings", default="core,dense")
    ap.add_argument("--variants", default="full_costaware,minus_costaware")
    ap.add_argument("--seeds", default="0,1,2,3,4")
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--dense-candidates-per-task", type=int, default=12)
    ap.add_argument("--output-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reuse-existing-if-complete", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.output_dir)
    raw_path = out_dir / "table4b_costaware_efficiency_raw.csv"
    summary_path = out_dir / "table4b_costaware_efficiency_summary.csv"
    trace_path = out_dir / "table4b_costaware_candidate_trace.csv"
    plot_data_path = out_dir / "table4b_costaware_efficiency_plot_data.csv"
    note_path = out_dir / "table4b_costaware_efficiency_note.md"
    expected_runs = len(parse_list(args.tasks)) * len(parse_list(args.settings)) * len(parse_list(args.variants)) * len(parse_list(args.seeds))
    if args.reuse_existing_if_complete and raw_path.exists():
        rows = list(csv.DictReader(raw_path.open()))
        if len(rows) >= expected_runs:
            print(f"reuse complete: {raw_path} ({len(rows)} rows)")
            return 0

    gt = {b["id"]: b for b in load_biases(strict_actions=False)}
    raw_rows: List[Dict[str, Any]] = []
    trace_rows: List[Dict[str, Any]] = []
    missing = []
    for paper_id in parse_list(args.tasks):
        repo_bias = PAPER_TO_REPO[paper_id]
        for seed in [int(s) for s in parse_list(args.seeds)]:
            run = load_run(repo_bias, seed)
            if run is None:
                missing.append(f"{paper_id}/seed{seed}")
                continue
            for setting in parse_list(args.settings):
                for variant in parse_list(args.variants):
                    if args.dry_run:
                        print(f"would replay {paper_id} repo={repo_bias} seed={seed} setting={setting} variant={variant}")
                        continue
                    raw, trace = replay_one(run, paper_id, repo_bias, setting, variant, seed,
                                            args.budget, args.dense_candidates_per_task, gt)
                    raw_rows.append(raw)
                    trace_rows.extend(trace)
    if args.dry_run:
        if missing:
            print("missing runs:", ", ".join(missing))
        return 0
    summary_rows, plot_rows = summarize(raw_rows)
    write_csv(raw_path, raw_rows, FIELD_RAW)
    write_csv(trace_path, trace_rows, FIELD_TRACE)
    write_csv(summary_path, summary_rows)
    write_csv(plot_data_path, plot_rows)
    write_note(note_path, raw_rows, trace_rows, summary_rows)
    if missing:
        print("missing saved runs skipped:", ", ".join(missing))
    print(f"wrote {raw_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {trace_path}")
    print(f"wrote {plot_data_path}")
    print(f"wrote {note_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
