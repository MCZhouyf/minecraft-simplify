"""Aggregate experiment runs into the paper tables (stage 7, evaluator).

Reads experiments/runs/<suite>/<run_id>/{summary.json, episodes.jsonl, k7.jsonl}
plus the K1 ground truth, writes CSVs under experiments/results/:

  table4_discovery.csv   per (bias, mode): precision / recall / decisions /
                         interventions / verify_steps  (also Table-1 ladder rows)
  table3_feedback.csv    per (bias, feedback): recall / episodes_to_decision
  table7_confound.csv    per (case, mode): gt_accepted / confound_rejected /
                         confound_wrongly_accepted
  lifelong_curve.csv     per (mode, episode): natural success rate, cum. proposals
  summary.json           run inventory + audit cross-check coverage (if K8 given)

Predicate match (GT vs candidate): same (target, property, comparator) AND
value match — exact for tiers/categories/bools/counts, |dy|<=8 for y_level,
window overlap for time ranges.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc_drift.datapack_gen import load_biases               # noqa: E402

RUNS_DIR = REPO / "experiments" / "runs"
OUT_DIR = REPO / "experiments" / "results"
GT = {b["id"]: b for b in load_biases(strict_actions=False)}

PAPER_ID = {
    "R2": "R1", "R5": "R2", "R6": "R3",
    "C2": "C1", "C3": "C2", "C4": "C3",
    "R1": "Stress", "R4": "Stress", "C1": "Boundary",
}
CORE_BIASES = ("R2", "R5", "R6", "C2", "C3", "C4")
RESOURCE_BIASES = {"R2", "R5", "R6"}
CONTEXT_BIASES = {"C2", "C3", "C4"}
RESET_TARGETS = {"inventory_count", "held_tool", "held_item"}
IN_WORLD_TARGETS = {"y_level", "time_of_day", "nearby_block", "sky_exposed", "station"}
OPERATIONAL_GT_VALUE = {
    # R2 was observed as operation-threshold 9 before the Java off-by-one fix;
    # current round3 finish expects nominal 8. Keep the column explicit.
    "R2": 8,
    # Current true-machine geometry/action semantics make these stricter
    # sufficient boundaries operationally identifiable. Nominal GT remains in
    # biases.yaml; see inworld_boundary_precision_diagnosis.md.
    "C2": 3,
    "C4": -7,
}


def value_match(target, gt_v, v):
    if target == "y_level":
        try:
            return abs(float(gt_v) - float(v)) <= 8
        except (TypeError, ValueError):
            return False
    if target == "time_of_day":
        try:
            a, b = map(float, gt_v)
            c, d = map(float, v)
            inter = max(0.0, min(b, d) - max(a, c))
            return inter / (b - a) >= 0.8
        except Exception:
            return False
    return str(gt_v) == str(v)


def property_match(target, gt_p, p):
    if target == "held_tool" and {str(gt_p), str(p)} <= {"pickaxe", "tier"}:
        return True
    if target == "time_of_day" and {str(gt_p), str(p)} <= {"clock", "time"}:
        return True
    return str(gt_p) == str(p)


def is_gt(bias_id, cand):
    g = GT[bias_id]["ground_truth"]
    return (cand["target"] == g["target"]
            and property_match(g["target"], g["property"], cand["property"])
            and cand["comparator"] == g["comparator"]
            and value_match(g["target"], g["value"], cand["value"]))


def paper_id(bias_id: str) -> str:
    return PAPER_ID.get(bias_id, bias_id)


def method_name(s: Dict[str, Any]) -> str:
    mode = s.get("mode")
    overrides = s.get("config_overrides") or {}
    if mode == "tcpg" and s.get("nota_reproposal"):
        return "tcpg_nota"
    if mode == "tcpg" and overrides.get("necessity_test") is False:
        return "tcpg_no_necessity"
    if mode == "tcpg" and overrides.get("neighbor_expand") is False:
        return "tcpg_no_neighbor"
    if mode == "tcpg" and overrides.get("posterior_mode") == "point":
        return "tcpg_no_dual_pool"
    if mode == "tcpg" and overrides.get("reproposal_signature_fallback") is False:
        return "tcpg_no_signature_fallback"
    if mode == "tcpg" and s.get("sim_cost_mode") == "flat":
        return "tcpg_flat_cost"
    return str(mode)


def bias_class(bias_id: str) -> str:
    if bias_id in RESOURCE_BIASES:
        return "resource_input"
    if bias_id in CONTEXT_BIASES:
        return "situational_constraint"
    return "appendix"


def edit_type(bias_id: str) -> str:
    return "rewrite" if bias_id in RESOURCE_BIASES else (
        "discovery" if bias_id in CONTEXT_BIASES else "appendix")


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parameter_error(bias_id: str, cand: Dict[str, Any],
                    operational: bool = False) -> Optional[float]:
    g = GT[bias_id]["ground_truth"]
    gt_v = OPERATIONAL_GT_VALUE.get(bias_id, g["value"]) if operational else g["value"]
    if g["target"] == "time_of_day":
        try:
            a, b = map(float, gt_v)
            c, d = map(float, cand["value"])
            return abs(((a + b) / 2.0) - ((c + d) / 2.0))
        except Exception:
            return None
    if isinstance(gt_v, (int, float)) or isinstance(cand.get("value"), (int, float)):
        gv, cv = _num(gt_v), _num(cand.get("value"))
        return abs(cv - gv) if gv is not None and cv is not None else None
    order = ["wooden", "stone", "iron", "diamond", "netherite"]
    if str(gt_v) in order and str(cand.get("value")) in order:
        return abs(order.index(str(cand["value"])) - order.index(str(gt_v)))
    return 0.0 if value_match(g["target"], gt_v, cand.get("value")) else None


def candidate_matches_signature(bias_id: str, cand: Dict[str, Any]) -> bool:
    g = GT[bias_id]["ground_truth"]
    return (cand.get("target") == g["target"]
            and property_match(g["target"], g["property"], cand.get("property"))
            and cand.get("comparator") == g["comparator"])


def gt_proposed(s: Dict[str, Any]) -> int:
    bias = s["bias"]
    if any(candidate_matches_signature(bias, c) for c in s.get("candidates", [])):
        return 1
    for e in s.get("_k7", []):
        p = e.get("payload", {})
        if e.get("type") == "validate" and p.get("ok"):
            if candidate_matches_signature(bias, p):
                return 1
        if e.get("type") in ("reproposal", "frontier_expand"):
            # Candidate IDs alone are not enough, but later validate/compile
            # events for these candidates will be caught above.
            continue
    if s.get("mode") == "llm_writeback":
        return int(any(candidate_matches_signature(bias, c)
                       for c in (s.get("_ccg") or {}).get("conditions", {}).values()))
    return 0


def gt_accepted_candidate(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    accepted, _ = _accepted_rejected(s)
    return next((c for c in accepted if is_gt(s["bias"], c)), None)


def natural_success_before_after(s: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    eps = s.get("_episodes") or []
    if not eps:
        return None, None
    return int(bool(eps[0].get("natural_success"))), int(bool(eps[-1].get("natural_success")))


def theory_order_hint(target: str) -> str:
    if target in RESET_TARGETS:
        return "O(log numeric/order frontier)"
    if target in IN_WORLD_TARGETS:
        return "O(log measurable boundary), O(k) navigation/check"
    return "empirical"


def load_runs(suite):
    for d in sorted((RUNS_DIR / suite).glob("*/")):
        sp = d / "summary.json"
        if not sp.exists():
            continue
        s = json.loads(sp.read_text())
        s["_dir"] = d
        s["_dir_run_id"] = d.name
        s["_episodes"] = [json.loads(l) for l in
                          (d / "episodes.jsonl").read_text().splitlines()] \
            if (d / "episodes.jsonl").exists() else []
        s["_k7"] = [json.loads(l) for l in
                    (d / "k7.jsonl").read_text().splitlines()] \
            if (d / "k7.jsonl").exists() else []
        s["_ccg"] = json.loads((d / "ccg.json").read_text()) \
            if (d / "ccg.json").exists() else {}
        yield s


def _discovery_run_prefix(s: Dict[str, Any]) -> Optional[str]:
    run_id = str(s.get("_dir_run_id") or s.get("run_id", ""))
    mode = str(s.get("mode", ""))
    feedback = str(s.get("feedback", "minimal"))
    seed = s.get("seed")
    suffix = f"_{mode}_{feedback}_s{seed}"
    if not run_id.endswith(suffix):
        return None
    return run_id[:-len(suffix)]


def is_counted_discovery_run(s: Dict[str, Any]) -> bool:
    """Filter out ad hoc debug/diagnostic runs from paper aggregation.

    The discovery directory often contains manual verification runs such as
    `R5_diagnostics` / `R5_fixcheck_*`. Those share the same bias/mode fields
    as matrix runs, so naive directory-wide aggregation silently pollutes the
    paper tables. Keep only canonical matrix runs and structured ablations.
    """
    if s.get("suite") != "discovery":
        return True
    bias = str(s.get("bias", ""))
    mode = str(s.get("mode", ""))
    prefix = _discovery_run_prefix(s)
    if prefix is None or not bias:
        return False
    if prefix == bias:
        return True
    if prefix == f"{bias}_nota":
        return mode == "tcpg" and bool(s.get("nota_reproposal"))
    overrides = s.get("config_overrides") or {}
    if prefix == f"{bias}_noNeigh":
        return mode == "tcpg" and overrides.get("neighbor_expand") is False
    if prefix == f"{bias}_noNec":
        return mode == "tcpg" and overrides.get("necessity_test") is False
    if prefix == f"{bias}_noSig":
        return mode == "tcpg" and overrides.get("reproposal_signature_fallback") is False
    if prefix == f"{bias}_flat":
        return mode == "tcpg" and s.get("sim_cost_mode") == "flat"
    if prefix in {f"{bias}_a0", f"{bias}_a05", f"{bias}_a1"}:
        return mode == "tcpg" and s.get("cost_alpha") is not None
    return False


def is_canonical_discovery_run(s: Dict[str, Any]) -> bool:
    """Canonical discovery matrix used by legacy table4_discovery/lifelong.

    Keep only the three base modes on the canonical `<bias>_<mode>_...` run ID.
    NOTA and all ablations belong in the Section-6 expanded tables, not in the
    legacy per-mode aggregate keyed only by `mode`.
    """
    if s.get("suite") != "discovery":
        return True
    bias = str(s.get("bias", ""))
    prefix = _discovery_run_prefix(s)
    return bool(bias) and prefix == bias


def _accepted_rejected(s):
    """Return (accepted, rejected) candidate dicts for scoring.

    Most modes set candidate.status in summary["candidates"]. llm_writeback,
    however, writes candidates straight to the CCG WITHOUT verification and
    returns before registering them in the candidate pool, so summary
    candidates is empty while ccg.conditions holds the written-back gates.
    For that mode we score the CCG's written-back conditions as 'accepted'
    (this is exactly the unverified-writeback ablation floor: everything the
    LLM proposed on failure is accepted as a gate). Rejected stays empty for
    llm_writeback since it never rejects."""
    if s.get("mode") == "llm_writeback":
        conds = list((s.get("_ccg") or {}).get("conditions", {}).values())
        return conds, []
    accepted = [c for c in s["candidates"] if c["status"] == "accepted"]
    rejected = [c for c in s["candidates"] if c["status"] == "rejected"]
    return accepted, rejected


def run_metrics(s):
    bias = s["bias"]
    accepted, rejected = _accepted_rejected(s)
    all_cands = accepted + rejected if s.get("mode") == "llm_writeback" \
        else s["candidates"]
    tp = sum(1 for c in accepted if is_gt(bias, c))
    precision = tp / len(accepted) if accepted else 0.0
    recall = 1.0 if tp else 0.0
    k7 = s["_k7"]
    interventions = sum(1 for e in k7 if e["type"] == "intervention_start")
    retries = sum(1 for e in k7 if e["type"] == "retry")
    voided = sum(1 for e in k7 if e["type"] == "undo"
                 and not (e["payload"].get("ok") and e["payload"].get("ctx_match")))
    proposals = sum(1 for e in k7 if e["type"] == "proposal")
    ep_to_decision = next((e["episode"] + 1 for e in s["_episodes"]
                           if any(v == "accepted" for v in e["decided"].values())),
                          None)
    confound = [c for c in all_cands if not is_gt(bias, c)]
    confound_accepted = [c for c in accepted if not is_gt(bias, c)]
    confound_rejected_set = [c for c in rejected if not is_gt(bias, c)]
    return {"precision": round(precision, 3), "recall": recall,
            "n_accepted": len(accepted), "n_rejected": len(rejected),
            "false_accepts": len(confound_accepted),
            "interventions": interventions, "retries": retries,
            "voided_obs": voided, "verify_steps": s.get("steps_used", 0),
            "proposal_calls": proposals, "episodes_to_decision": ep_to_decision,
            "gt_accepted": int(any(is_gt(bias, c) for c in accepted)),
            "confound_rejected": int(len(confound_rejected_set) > 0),
            "confound_wrongly_accepted": int(len(confound_accepted) > 0)}


def signature_metrics(s: Dict[str, Any]) -> Dict[str, Any]:
    """Discovery-level scoring that ignores numeric boundary error.

    In-world runs can identify the correct causal signature while the exact
    operational threshold is shifted by world geometry. Table 4 keeps the
    parameter error; these metrics answer whether the right cause was found.
    """
    bias = s["bias"]
    accepted, _ = _accepted_rejected(s)
    accepted_sig = [c for c in accepted if candidate_matches_signature(bias, c)]
    precision = len(accepted_sig) / len(accepted) if accepted else 0.0
    recall = 1.0 if accepted_sig else 0.0
    f1 = round(2 * precision * recall / (precision + recall), 3) \
        if (precision + recall) else 0.0
    return {
        "signature_accepted": int(bool(accepted_sig)),
        "precision_sig": round(precision, 3),
        "recall_sig": recall,
        "f1_sig": f1,
    }


def write_csv(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def _read_csv_dicts(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def aggregate(group):
    """Mean over seeds for numeric fields."""
    keys = [k for k, v in group[0].items() if isinstance(v, (int, float))]
    out = dict(group[0])
    for k in keys:
        vals = [g.get(k) for g in group if isinstance(g.get(k), (int, float))]
        out[k] = round(sum(vals) / len(vals), 3) if vals else None
    out["n_seeds"] = len(group)
    return out


def mean(vals: Iterable[Any]) -> Optional[float]:
    xs = [float(v) for v in vals if v is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def rate(vals: Iterable[Any]) -> Optional[float]:
    xs = [int(v) for v in vals if v is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def _group_rows(rows: List[Dict[str, Any]], keys: Tuple[str, ...],
                metrics: Dict[str, str]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r.get(k) for k in keys)].append(r)
    out = []
    for key, group in sorted(groups.items()):
        row = {k: v for k, v in zip(keys, key)}
        for name, how in metrics.items():
            if how == "mean":
                row[name] = mean(g.get(name) for g in group)
            elif how == "rate":
                row[name] = rate(g.get(name) for g in group)
            elif how == "sum":
                row[name] = sum(float(g.get(name, 0) or 0) for g in group)
        row["n_runs"] = len(group)
        out.append(row)
    return out


ABLATION_LABELS = {
    "full": "full_tcpg",
    "fulltcpg": "full_tcpg",
    "no_dualpool": "minus_dual_pool_posterior",
    "noNec": "minus_necessity_confirmation",
    "noNeigh": "minus_neighbor_frontier",
    "a0": "cost_alpha_0",
    "floor2": "min_verification_floor_2",
}


def _component_ablation_name(s: Dict[str, Any]) -> Optional[str]:
    prefix = _discovery_run_prefix(s)
    bias = str(s.get("bias", ""))
    if not prefix or not bias:
        return None
    for short, label in ABLATION_LABELS.items():
        if prefix == f"abl_{short}_{bias}":
            return label
    return None


def _prefixed_case_run(s: Dict[str, Any], prefixes: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    run_id = str(s.get("_dir_run_id") or s.get("run_id", ""))
    mode = str(s.get("mode", ""))
    feedback = str(s.get("feedback", "minimal"))
    seed = s.get("seed")
    suffix = f"_{mode}_{feedback}_s{seed}"
    if not run_id.endswith(suffix):
        return None
    prefix_case = run_id[:-len(suffix)]
    for prefix in prefixes:
        marker = f"{prefix}_"
        if prefix_case.startswith(marker):
            return prefix, prefix_case[len(marker):]
    return None


def _interventions_before_gt(s: Dict[str, Any]) -> Optional[int]:
    accepted, _ = _accepted_rejected(s)
    gt_cids = {c.get("cid") for c in accepted if is_gt(s["bias"], c)}
    if not gt_cids:
        return None
    count = 0
    for e in s.get("_k7", []):
        typ = e.get("type")
        if typ == "intervention_start":
            count += 1
        if typ == "writeback":
            p = e.get("payload", {})
            if p.get("decision") == "accepted" and p.get("cid") in gt_cids:
                return count
    return count


def _cost_efficiency_run_row(s: Dict[str, Any], prefix: str, case: str,
                             suite: str) -> Dict[str, Any]:
    accepted, _ = _accepted_rejected(s)
    k7 = s.get("_k7", [])
    return {
        "suite": suite,
        "case": case,
        "repo_bias": s.get("bias", ""),
        "paper_id": paper_id(s.get("bias", "")),
        "method": "full_costaware" if prefix == "costfull" else "minus_costaware_alpha0_floor0",
        "run_id": s.get("run_id", ""),
        "seed": s.get("seed"),
        "gt_accepted": int(any(is_gt(s["bias"], c) for c in accepted)),
        "interventions": sum(1 for e in k7 if e.get("type") == "intervention_start"),
        "verify_steps": s.get("steps_used", 0),
        "interventions_before_gt": _interventions_before_gt(s),
        "voided_obs": sum(1 for e in k7 if e.get("type") == "undo"
                          and not (e.get("payload", {}).get("ok")
                                   and e.get("payload", {}).get("ctx_match"))),
    }


def write_cost_efficiency_table() -> None:
    rows = []
    for suite in ("discovery", "confound"):
        for s in load_runs(suite):
            parsed = _prefixed_case_run(s, ("costfull", "nocost"))
            if parsed is None:
                continue
            prefix, case = parsed
            rows.append(_cost_efficiency_run_row(s, prefix, case, suite))

    bases = {(r["suite"], r["case"], r["seed"]): r for r in rows
             if r["method"] == "full_costaware"}
    out = []
    for row in rows:
        base = bases.get((row["suite"], row["case"], row["seed"]))
        if row["method"] == "full_costaware":
            if not any(r["method"] != "full_costaware"
                       and (r["suite"], r["case"], r["seed"]) ==
                       (row["suite"], row["case"], row["seed"]) for r in rows):
                continue
            row["paired_baseline_run_id"] = ""
            row["delta_interventions"] = ""
            row["delta_verify_steps"] = ""
            row["delta_interventions_before_gt"] = ""
            out.append(row)
            continue
        if base is None:
            continue
        row["paired_baseline_run_id"] = base["run_id"]
        row["delta_interventions"] = row["interventions"] - base["interventions"]
        row["delta_verify_steps"] = row["verify_steps"] - base["verify_steps"]
        a, b = row["interventions_before_gt"], base["interventions_before_gt"]
        row["delta_interventions_before_gt"] = (
            "" if a is None or b is None else a - b)
        out.append(row)
    write_csv(OUT_DIR / "table6_cost_efficiency.csv", out)


def write_component_ablation_table() -> None:
    """Per-run measured component ablations for Table 6.

    These runs are intentionally outside the canonical discovery matrix, so
    table2/table7 guards remain unchanged. The table reports exactly what the
    ablation run observed: unique GT value recovery, false accepts, and a simple
    non-monotonicity/noise proxy from aborted or voided intervention evidence.
    """
    rows = []
    baselines: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    for s in load_runs("discovery"):
        ablation = _component_ablation_name(s)
        if ablation is None:
            continue
        bias = s["bias"]
        if bias == "C4":
            continue
        accepted, rejected = _accepted_rejected(s)
        accepted_gt = [c for c in accepted if is_gt(bias, c)]
        accepted_sig = [c for c in accepted if candidate_matches_signature(bias, c)]
        false_accepts = [c for c in accepted if not candidate_matches_signature(bias, c)]
        k7 = s.get("_k7", [])
        interventions = sum(1 for e in k7 if e.get("type") == "intervention_start")
        voided = sum(1 for e in k7 if e.get("type") == "undo"
                     and not (e.get("payload", {}).get("ok")
                              and e.get("payload", {}).get("ctx_match")))
        aborts = sum(1 for e in k7 if e.get("type") == "trigger_abort")
        row = {
            "ablation": ablation,
            "repo_bias": bias,
            "paper_id": paper_id(bias),
            "class": bias_class(bias),
            "run_id": s.get("run_id"),
            "seed": s.get("seed"),
            "gt_accepted": int(bool(accepted_gt)),
            "signature_accepted": int(bool(accepted_sig)),
            "unique_gt_value_accepted": int(len(accepted_gt) == 1),
            "false_accepts": len(false_accepts),
            "n_rejected": len(rejected),
            "interventions": interventions,
            "steps_used": s.get("steps_used", 0),
            "nonmonotonic_proxy_rate": round((voided + aborts) / max(interventions, 1), 3),
            "voided_obs": voided,
            "trigger_aborts": aborts,
        }
        rows.append(row)
        if ablation == "full_tcpg":
            baselines[(bias, s.get("seed"))] = row
    paired_keys = {
        (row["repo_bias"], row["seed"])
        for row in rows
        if row["ablation"] != "full_tcpg"
        and (row["repo_bias"], row["seed"]) in baselines
    }
    out_rows = []
    for row in rows:
        base = baselines.get((row["repo_bias"], row["seed"]))
        if row["ablation"] == "full_tcpg":
            if (row["repo_bias"], row["seed"]) not in paired_keys:
                continue
            row["paired_baseline_run_id"] = ""
            row["delta_gt_accepted"] = ""
            row["delta_signature_accepted"] = ""
            row["delta_false_accepts"] = ""
            row["delta_steps_used"] = ""
            row["delta_nonmonotonic_proxy_rate"] = ""
            out_rows.append(row)
            continue
        if base is None:
            continue
        row["paired_baseline_run_id"] = base["run_id"]
        row["delta_gt_accepted"] = row["gt_accepted"] - base["gt_accepted"]
        row["delta_signature_accepted"] = row["signature_accepted"] - base["signature_accepted"]
        row["delta_false_accepts"] = row["false_accepts"] - base["false_accepts"]
        row["delta_steps_used"] = row["steps_used"] - base["steps_used"]
        row["delta_nonmonotonic_proxy_rate"] = round(
            row["nonmonotonic_proxy_rate"] - base["nonmonotonic_proxy_rate"], 3)
        out_rows.append(row)
    write_csv(OUT_DIR / "table6_component_ablation_runs.csv", out_rows)


def write_c4_dualpool_trace() -> None:
    rows = []
    wanted = {
        "abl_fulltcpg_C4_tcpg_minimal_s1": "full_tcpg",
        "abl_no_dualpool_C4_tcpg_minimal_s1": "minus_dual_pool_posterior",
    }
    for s in load_runs("discovery"):
        run_id = s.get("run_id")
        if run_id not in wanted:
            continue
        for c in s.get("candidates", []):
            if (c.get("target") == "y_level"
                    and c.get("comparator") == "<="
                    and int(float(c.get("value"))) == -8):
                rows.append({
                    "run_id": run_id,
                    "ablation": wanted[run_id],
                    "repo_bias": "C4",
                    "paper_id": paper_id("C4"),
                    "candidate": "y_level<=-8",
                    "n_pos": c.get("n_pos", 0),
                    "k_pos": c.get("k_pos", 0),
                    "n_neg": c.get("n_neg", 0),
                    "k_neg": c.get("k_neg", 0),
                    "status": c.get("status", ""),
                    "note": ("qualitative trace only; C4 excluded from table6 "
                             "quantitative delta because y_level has tolerance noise"),
                })
                break
    write_csv(OUT_DIR / "table6_c4_dualpool_trace.csv", rows)


def run_rows_for_paper() -> List[Dict[str, Any]]:
    rows = []
    for suite in ("discovery", "feedback_ladder", "confound"):
        for s in load_runs(suite):
            if suite == "discovery" and not is_counted_discovery_run(s):
                continue
            bias = s["bias"]
            if bias not in GT:
                continue
            accepted, rejected = _accepted_rejected(s)
            accepted_gt = [c for c in accepted if is_gt(bias, c)]
            accepted_sig = [c for c in accepted if candidate_matches_signature(bias, c)]
            before, after = natural_success_before_after(s)
            k7 = s.get("_k7", [])
            sig = signature_metrics(s)
            rows.append({
                "suite": suite,
                "run_id": s["run_id"],
                "repo_bias": bias,
                "paper_id": paper_id(bias),
                "class": bias_class(bias),
                "edit_type": edit_type(bias),
                "method": method_name(s),
                "mode": s.get("mode"),
                "seed": s.get("seed"),
                "gt_proposed": gt_proposed(s),
                "gt_accepted": int(bool(accepted_gt)),
                "signature_accepted": sig["signature_accepted"],
                "precision": run_metrics(s)["precision"],
                "recall": run_metrics(s)["recall"],
                "f1": None,
                "precision_sig": sig["precision_sig"],
                "recall_sig": sig["recall_sig"],
                "f1_sig": sig["f1_sig"],
                "param_error_nominal": parameter_error(bias, accepted_sig[0])
                    if accepted_sig else None,
                "param_error_operational": parameter_error(bias, accepted_sig[0], True)
                    if accepted_sig else None,
                "unique_gt_value_accepted": int(len(accepted_gt) == 1),
                "llm_underestimate": int(
                    bool(accepted_sig)
                    and s.get("mode") == "llm_writeback"
                    and (_num(accepted_sig[0].get("value")) is not None)
                    and (_num(GT[bias]["ground_truth"]["value"]) is not None)
                    and _num(accepted_sig[0].get("value")) < _num(GT[bias]["ground_truth"]["value"])
                ),
                "success_before": before,
                "success_after": after,
                "steps_used": s.get("steps_used", 0),
                "blind_replans": sum(1 for e in k7 if e.get("type") == "retry"),
                "interventions": sum(1 for e in k7 if e.get("type") == "intervention_start"),
                "writeback_errors": run_metrics(s)["confound_wrongly_accepted"],
                "missed_gt": int(not bool(accepted_gt)),
                "missed_signature": int(not bool(accepted_sig)),
                "aborted": int(bool(s.get("aborted"))),
            })
    for r in rows:
        p, rec = r["precision"], r["recall"]
        r["f1"] = round(2 * p * rec / (p + rec), 3) if (p + rec) else 0.0
    return rows


def write_paper_tables():
    rows = run_rows_for_paper()
    core = [r for r in rows if r["repo_bias"] in CORE_BIASES and r["suite"] == "discovery"]

    write_csv(OUT_DIR / "table1_planning.csv", _group_rows(
        core, ("method", "class"),
        {"success_before": "rate", "success_after": "rate",
         "steps_used": "mean", "blind_replans": "mean", "writeback_errors": "rate"}))

    write_csv(OUT_DIR / "table2_recall.csv", _group_rows(
        core, ("method", "class"),
        {"gt_proposed": "rate", "signature_accepted": "rate",
         "gt_accepted": "rate", "recall_sig": "mean", "recall": "mean"}))

    write_csv(OUT_DIR / "table3_calibration.csv", _group_rows(
        core, ("edit_type", "method"),
        {"precision": "mean", "recall": "mean", "f1": "mean"}))

    write_csv(OUT_DIR / "table4_param.csv", _group_rows(
        core, ("paper_id", "repo_bias", "method"),
        {"param_error_nominal": "mean", "param_error_operational": "mean",
         "unique_gt_value_accepted": "rate", "llm_underestimate": "rate"}))

    write_csv(OUT_DIR / "table5_writeback.csv", _group_rows(
        core, ("method",),
        {"success_before": "rate", "success_after": "rate",
         "writeback_errors": "rate", "blind_replans": "mean"}))

    write_csv(OUT_DIR / "table6_ablation.csv", _group_rows(
        [r for r in core if r["method"].startswith("tcpg")],
        ("method", "class"),
        {"precision_sig": "mean", "recall_sig": "mean", "f1_sig": "mean",
         "steps_used": "mean", "interventions": "mean"}))
    write_component_ablation_table()
    write_c4_dualpool_trace()
    write_cost_efficiency_table()
    write_downstream_success_tables()

    table7 = _group_rows(
        core, ("method", "class"),
        {"interventions": "mean", "writeback_errors": "rate",
         "missed_signature": "rate", "missed_gt": "rate"})
    for row in table7:
        target = next((GT[r["repo_bias"]]["ground_truth"]["target"] for r in core
                       if r["method"] == row["method"] and r["class"] == row["class"]), "")
        row["theory_trend"] = theory_order_hint(target)
    write_csv(OUT_DIR / "table7_theory.csv", table7)
    write_significance_table(core)


def write_downstream_success_tables() -> None:
    """Expose Stage-B downstream success as the success columns for Tables 2-4.

    The downstream harness owns the actual computation; evaluate.py only copies
    the latest CSV into stable paper-table fragments so a normal re-evaluate
    refreshes every result file readers expect.
    """
    path = OUT_DIR / "table_downstream_full.csv"
    if not path.exists():
        path = OUT_DIR / "table_downstream.csv"
    rows = _read_csv_dicts(path)
    if not rows:
        return
    table2 = [{
        "method": r["method"],
        "paper_id": r["paper_id"],
        "condition": r["condition"],
        "success_rate": round(float(r["success_rate"]), 3),
        "ci_low": round(float(r["ci_low"]), 3),
        "ci_high": round(float(r["ci_high"]), 3),
        "n": int(r["n"]),
    } for r in rows]
    write_csv(OUT_DIR / "table2_downstream_success.csv", table2)

    table3 = [{
        "paper_id": r["paper_id"],
        "writeback_success": round(float(r["success_rate"]), 3),
        "n": int(r["n"]),
    } for r in rows if r["method"] == "after" and r["condition"] == "drift"]
    write_csv(OUT_DIR / "table3_downstream_success.csv", table3)

    variants = ["after", "minus_nota", "minus_boundary",
                "minus_dual_pool", "minus_costaware"]
    table4 = []
    for variant in variants:
        drift = [r for r in rows if r["method"] == variant and r["condition"] == "drift"]
        if not drift:
            continue
        k = sum(int(r["k"]) for r in drift)
        n = sum(int(r["n"]) for r in drift)
        rate_v = k / n if n else 0.0
        # Keep this stdlib-only and aligned with iap_downstream.metrics.wilson_ci.
        import math
        z = 1.96
        denom = 1.0 + z * z / n if n else 1.0
        center = (rate_v + z * z / (2 * n)) / denom if n else 0.0
        half = (z * math.sqrt(rate_v * (1 - rate_v) / n + z * z / (4 * n * n)) / denom) if n else 0.0
        table4.append({
            "variant": variant,
            "success_rate": round(rate_v, 3),
            "ci_low": round(max(0.0, center - half), 3),
            "ci_high": round(min(1.0, center + half), 3),
            "n": n,
        })
    write_csv(OUT_DIR / "table4_downstream_success.csv", table4)


def _mean_float(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _bootstrap_diff(a: List[float], b: List[float],
                    n: int = 10_000, seed: int = 17) -> Tuple[float, float, float]:
    rng = random.Random(seed)
    if not a or not b:
        return 0.0, 0.0, 0.0
    diffs = []
    for _ in range(n):
        aa = [a[rng.randrange(len(a))] for _ in a]
        bb = [b[rng.randrange(len(b))] for _ in b]
        diffs.append(_mean_float(bb) - _mean_float(aa))
    diffs.sort()
    return (_mean_float(b) - _mean_float(a),
            diffs[int(0.025 * (n - 1))],
            diffs[int(0.975 * (n - 1))])


def _permutation_p(a: List[float], b: List[float],
                   n: int = 20_000, seed: int = 23) -> float:
    rng = random.Random(seed)
    obs = abs(_mean_float(b) - _mean_float(a))
    pool = list(a) + list(b)
    if not a or not b or obs == 0:
        return 1.0
    hits = 0
    for _ in range(n):
        rng.shuffle(pool)
        aa = pool[:len(a)]
        bb = pool[len(a):]
        if abs(_mean_float(bb) - _mean_float(aa)) >= obs:
            hits += 1
    return (hits + 1) / (n + 1)


def _holm(ps: List[float]) -> List[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adjusted = [0.0] * len(ps)
    running = 0.0
    m = len(ps)
    for rank, idx in enumerate(order):
        val = min(1.0, (m - rank) * ps[idx])
        running = max(running, val)
        adjusted[idx] = running
    return adjusted


def write_significance_table(core: List[Dict[str, Any]]) -> None:
    """Bootstrap 95% CIs plus Holm-corrected permutation p-values.

    Small-n warning is intentional for situational rows: the table quantifies
    uncertainty without pretending three seeds have high power.
    """
    rows_spec = []

    context = [r for r in core if r["class"] == "situational_constraint"]
    vals_tcpg = [float(r["signature_accepted"]) for r in context if r["method"] == "tcpg"]
    vals_nota = [float(r["signature_accepted"]) for r in context if r["method"] == "tcpg_nota"]
    rows_spec.append({
        "comparison": "NOTA situational recall gain",
        "metric": "signature_accepted_rate",
        "group_a": "tcpg",
        "group_b": "tcpg_nota",
        "a": vals_tcpg,
        "b": vals_nota,
        "note": "situational class; n is limited by available seeds",
    })

    for a_method, b_method in (("llm_writeback", "tcpg"),
                               ("llm_writeback", "tcpg_nota")):
        rows_spec.append({
            "comparison": "verification necessity discovery F1",
            "metric": "f1_sig",
            "group_a": a_method,
            "group_b": b_method,
            "a": [float(r["f1_sig"]) for r in core if r["method"] == a_method],
            "b": [float(r["f1_sig"]) for r in core if r["method"] == b_method],
            "note": "all core tasks; bootstrap over runs",
        })

    raw_ps = [_permutation_p(r["a"], r["b"]) for r in rows_spec]
    holm_ps = _holm(raw_ps)
    out = []
    for spec, raw_p, holm_p in zip(rows_spec, raw_ps, holm_ps):
        diff, lo, hi = _bootstrap_diff(spec["a"], spec["b"])
        out.append({
            "comparison": spec["comparison"],
            "metric": spec["metric"],
            "group_a": spec["group_a"],
            "group_b": spec["group_b"],
            "mean_a": round(_mean_float(spec["a"]), 3),
            "mean_b": round(_mean_float(spec["b"]), 3),
            "diff_b_minus_a": round(diff, 3),
            "ci95_low": round(lo, 3),
            "ci95_high": round(hi, 3),
            "p_permutation": round(raw_p, 5),
            "p_holm": round(holm_p, 5),
            "n_a": len(spec["a"]),
            "n_b": len(spec["b"]),
            "note": spec["note"],
        })
    write_csv(OUT_DIR / "table_significance.csv", out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", help="path to mcdrift_audit.jsonl for K8 cross-check")
    args = ap.parse_args(argv)
    inventory = {}

    # ---- Table 4 / Table 1 ladder + lifelong (discovery suite) ----
    by_key, curve = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for s in load_runs("discovery"):
        if not is_canonical_discovery_run(s):
            continue
        m = run_metrics(s)
        by_key[(s["bias"], s["mode"])].append(m)
        for e in s["_episodes"]:
            curve[(s["mode"], e["episode"])]["succ"].append(int(e["natural_success"]))
        inventory[s["run_id"]] = s["suite"]
    rows4 = [{"bias": b, "mode": mo, **aggregate(g)}
             for (b, mo), g in sorted(by_key.items())]
    write_csv(OUT_DIR / "table4_discovery.csv", rows4)
    rows_c = [{"mode": mo, "episode": ep,
               "natural_success_rate": round(sum(v["succ"]) / len(v["succ"]), 3),
               "n": len(v["succ"])}
              for (mo, ep), v in sorted(curve.items())]
    write_csv(OUT_DIR / "lifelong_curve.csv", rows_c)

    # ---- Table 3 (feedback ladder) ----
    by_fb = defaultdict(list)
    for s in load_runs("feedback_ladder"):
        by_fb[(s["bias"], s["feedback"])].append(run_metrics(s))
        inventory[s["run_id"]] = s["suite"]
    rows3 = [{"bias": b, "feedback": fb, **aggregate(g)}
             for (b, fb), g in sorted(by_fb.items())]
    write_csv(OUT_DIR / "table3_feedback.csv", rows3)

    # ---- Table 7 (confounds) ----
    by_case = defaultdict(list)
    for s in load_runs("confound"):
        parts = s["run_id"].split("_")
        prefix = parts[0] if parts else ""
        if len(parts) > 2 and parts[0] == "no" and parts[1] == "dualpool":
            prefix = "no_dualpool"
            case = parts[2]
        elif prefix in {"nota", "fulltcpg"} and len(parts) > 1:
            case = parts[1]
        else:
            case = parts[0]
        if s["mode"] == "tcpg" and prefix == "nota":
            mode = "tcpg_nota"
        elif s["mode"] == "tcpg" and prefix == "fulltcpg":
            mode = "tcpg_full"
        elif s["mode"] == "tcpg" and prefix == "no_dualpool":
            mode = "tcpg_no_dual_pool"
        else:
            mode = s["mode"]
        by_case[(case, mode)].append(run_metrics(s))
        inventory[s["run_id"]] = s["suite"]
    rows7 = [{"case": c, "mode": mo, **aggregate(g)}
             for (c, mo), g in sorted(by_case.items())]
    write_csv(OUT_DIR / "table7_confound.csv", rows7)

    # ---- Paper Section 6 expanded tables (Tables 1-7) ----
    write_paper_tables()

    # ---- K8 cross-check (optional) ----
    audit_cov = None
    if args.audit and Path(args.audit).exists():
        audit = [json.loads(l) for l in Path(args.audit).read_text().splitlines()]
        blocks = {a["bias_id"] for a in audit if a.get("decision") == "block"}
        seen = {s["bias"] for s in load_runs("discovery")}
        audit_cov = {"biases_run": sorted(seen),
                     "biases_with_engine_blocks": sorted(blocks & seen),
                     "coverage": round(len(blocks & seen) / max(len(seen), 1), 3)}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(
        {"n_runs": len(inventory), "audit_cross_check": audit_cov}, indent=2))
    print(f"runs aggregated: {len(inventory)}; results -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
