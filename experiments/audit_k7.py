"""Per-run K7 audit (stage 3, manual sec.2 step 4 + sec.3).

Two jobs, both read straight from a run directory's k7.jsonl + summary.json:

  EXECUTION HEALTH
  - count of every K7 event type
  - trigger_abort broken down by reason (plan_fail / undo_fail / ctx_unrestorable)
  - ctx_resync, neighbor_expand (+ candidates added)
  - writeback by decision (accepted / rejected / confirmed_known)
  - the GT candidate's posterior q_hat trajectory across episodes (does the true
    cause's posterior climb across the accept threshold, cf. paper 6.6 R6)

  COST STRATIFICATION (paper 4.4, the round-3 addition)
  - verify every candidate's logged intervention cost against the floor model:
      sim_verifiable  -> floor mode: cost == max(sim_verify_cost, est_steps)
                         flat  mode: cost == sim_verify_cost
      not sim_verif.  -> cost == est_steps   (real exploration)
    A mismatch is a BLOCKING audit failure: the cost layering that tables 6/6b
    depend on is not actually in the data. Also reports the resource-input vs
    situational-constraint mean cost split so the layering is visible at a glance.

Importable:  audit_dir(run_dir) -> dict
CLI:         python3 experiments/audit_k7.py [--runs DIR] [--run PATH]
Exit code 1 if any cost-stratification mismatch is found.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RUNS_DIR = REPO / "experiments" / "runs"
_TOL = 0.011                              # logged costs are rounded to 2 dp

try:
    from experiments.evaluate import is_gt as _is_gt
except Exception:                          # pragma: no cover
    _is_gt = None


def _load_jsonl(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _expected_cost(sim_verifiable, est, sim_cost, sim_mode):
    if not sim_verifiable:
        return est
    if sim_mode == "flat":
        return sim_cost
    return max(sim_cost, est)


def _gt_cid(bias, candidates):
    """cid of the candidate matching the GT predicate, or None."""
    for c in candidates:
        if _is_gt is not None and bias:
            try:
                if _is_gt(bias, c):
                    return c.get("cid")
                continue
            except Exception:
                pass
    return None


def audit_dir(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text())
    k7 = _load_jsonl(run_dir / "k7.jsonl")
    bias = summary.get("bias")
    candidates = summary.get("candidates", [])

    types = Counter(e.get("type") for e in k7)
    abort_reasons = Counter(
        e["payload"].get("reason") for e in k7
        if e.get("type") == "trigger_abort")
    writeback_decisions = Counter(
        e["payload"].get("decision") for e in k7
        if e.get("type") == "writeback")
    neighbor_added = sum(e["payload"].get("added", 0) for e in k7
                         if e.get("type") == "neighbor_expand")

    # ---- cost stratification verification ----
    cost_violations = []
    class_cost = defaultdict(list)         # "sim"/"real" -> [cost,...]
    for e in k7:
        if e.get("type") != "cost_model":
            continue
        p = e["payload"]
        sim_cost = float(p.get("sim_verify_cost", 2.0))
        sim_mode = str(p.get("sim_cost_mode", "floor"))
        for cid, cd in p.get("costs", {}).items():
            est = float(cd.get("est_steps", 0.0))
            sv = bool(cd.get("sim_verifiable"))
            cost = float(cd.get("cost", 0.0))
            exp = _expected_cost(sv, est, sim_cost, sim_mode)
            class_cost["sim" if sv else "real"].append(cost)
            if abs(cost - exp) > _TOL:
                cost_violations.append(
                    {"cid": cid, "sim_verifiable": sv, "est_steps": est,
                     "cost": cost, "expected": exp, "mode": sim_mode})

    # cross-check: intervention_start cost must equal the cost_model cost
    cm_cost = {}
    for e in k7:
        if e.get("type") == "cost_model":
            for cid, cd in e["payload"].get("costs", {}).items():
                cm_cost[cid] = float(cd.get("cost", 0.0))
    for e in k7:
        if e.get("type") == "intervention_start":
            cid = e["payload"].get("cid")
            c = e["payload"].get("cost")
            if cid in cm_cost and c is not None \
                    and abs(float(c) - cm_cost[cid]) > _TOL:
                cost_violations.append(
                    {"cid": cid, "intervention_start_cost": float(c),
                     "cost_model_cost": cm_cost[cid], "mismatch": "is_vs_cm"})

    # ---- GT posterior q_hat trajectory ----
    gt_cid = _gt_cid(bias, candidates)
    q_traj = [round(float(e["payload"].get("q_hat")), 4) for e in k7
              if e.get("type") == "posterior_update"
              and e["payload"].get("cid") == gt_cid
              and e["payload"].get("q_hat") is not None] if gt_cid else []

    mean = (lambda xs: round(sum(xs) / len(xs), 2) if xs else None)
    return {
        "run_id": summary.get("run_id"),
        "bias": bias, "mode": summary.get("mode"), "suite": summary.get("suite"),
        "event_counts": dict(types),
        "trigger_abort_by_reason": dict(abort_reasons),
        "ctx_resync": types.get("ctx_resync", 0),
        "neighbor_expand": types.get("neighbor_expand", 0),
        "neighbor_candidates_added": neighbor_added,
        "writeback_by_decision": dict(writeback_decisions),
        "interventions": types.get("intervention_start", 0),
        "cost_mode": summary.get("sim_cost_mode"),
        "mean_cost_resource_input": mean(class_cost["sim"]),
        "mean_cost_situational": mean(class_cost["real"]),
        "cost_violations": cost_violations,
        "gt_cid": gt_cid,
        "gt_q_hat_trajectory": q_traj,
        "gt_q_hat_final": q_traj[-1] if q_traj else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default=str(RUNS_DIR),
                    help="runs root to walk (default experiments/runs)")
    ap.add_argument("--run", default=None,
                    help="audit a single run directory instead of walking --runs")
    ap.add_argument("--json", action="store_true", help="emit raw JSON per run")
    args = ap.parse_args(argv)

    if args.run:
        dirs = [Path(args.run)]
    else:
        dirs = sorted(p.parent for p in Path(args.runs).rglob("summary.json"))

    total_violations = 0
    for d in dirs:
        rep = audit_dir(d)
        total_violations += len(rep["cost_violations"])
        if args.json:
            print(json.dumps(rep, ensure_ascii=False))
            continue
        print(f"\n=== {rep['run_id']}  ({rep['bias']}/{rep['mode']}) ===")
        print(f"  events: {rep['event_counts']}")
        if rep["trigger_abort_by_reason"]:
            print(f"  trigger_abort: {rep['trigger_abort_by_reason']} "
                  f"| ctx_resync: {rep['ctx_resync']}")
        if rep["writeback_by_decision"]:
            print(f"  writeback: {rep['writeback_by_decision']}")
        if rep["neighbor_expand"]:
            print(f"  neighbor_expand: {rep['neighbor_expand']} "
                  f"(+{rep['neighbor_candidates_added']} cands)")
        print(f"  cost[{rep['cost_mode']}] resource-input mean="
              f"{rep['mean_cost_resource_input']} "
              f"situational mean={rep['mean_cost_situational']}")
        if rep["gt_cid"]:
            print(f"  GT q_hat: {rep['gt_q_hat_trajectory']} "
                  f"-> final {rep['gt_q_hat_final']}")
        if rep["cost_violations"]:
            print(f"  !! {len(rep['cost_violations'])} COST VIOLATION(S):")
            for v in rep["cost_violations"]:
                print(f"     {v}")

    print(f"\naudited {len(dirs)} run(s) | "
          f"{total_violations} cost-stratification violation(s)")
    return 1 if total_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
