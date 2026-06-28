"""Cost-layering / cost-ablation report (stage 3, manual sec.3 + paper 4.4, 6.4).

Turns a runs tree into the two cost-stratification tables the round-3 batch is
meant to produce. Reuses evaluate.is_gt so GT matching agrees with the main
evaluator.

  table4_by_class.csv  -- discovery runs grouped by candidate class x mode.
    Classes follow paper 4.4: resource-input  (GT target is sim_verifiable:
    inventory_count / held_tool / held_item) vs situational-constraint
    (nearby_block / y_level / time_of_day, reached by real exploration).
    Columns: precision / recall / f1 / error_writeback_rate, plus
    mean_neff = mean over the class of the GT candidate's two-sided effective
    observation count min(n_pos, n_neg). This is the empirical form of the
    paper's "same budget -> resource-input accrues more effective observations"
    claim (sec.4.5 budget-sample relation): cheaper contrasts buy more n_eff.

  table6b_cost_ablation.csv -- cost-sensitivity ablation (paper table 6b).
    Grouped by alpha (read from the cost_model K7 event, falling back to
    summary.cost_alpha). Columns: interventions_before_gt (how many
    interventions were spent before the true cause was accepted -- high for the
    cost-blind alpha=0 run that chases expensive neighbours first), gt_accepted,
    verify_steps. Intended to be run on the depth-gate bias (C4) under
    --cost-alpha {0,0.5,1} --min-floor 0.

Importable:  build(runs_dir) -> {"by_class": [...], "ablation": [...]}
CLI:         python3 experiments/cost_layering_report.py [--runs DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from experiments.evaluate import is_gt                       # noqa: E402
from Adam.tcpg.compiler import SIM_VERIFIABLE_TARGETS        # noqa: E402

import yaml                                                  # noqa: E402

RUNS_DIR = REPO / "experiments" / "runs"
OUT_DIR = REPO / "experiments" / "results"
_BIASES = yaml.safe_load(
    (REPO / "mc_drift" / "biases" / "biases.yaml").read_text())["biases"]
GT_TARGET = {b["id"]: b["ground_truth"]["target"] for b in _BIASES}


def bias_class(bias_id: str) -> str:
    """resource_input if the GT target is reached by take/craft/equip
    (sim_verifiable), else situational_constraint (move/wait/place)."""
    return ("resource_input"
            if GT_TARGET.get(bias_id) in SIM_VERIFIABLE_TARGETS
            else "situational_constraint")


def _load_jsonl(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _runs(runs_dir: Path):
    for sp in sorted(Path(runs_dir).rglob("summary.json")):
        s = json.loads(sp.read_text())
        s["_dir"] = sp.parent
        s["_k7"] = _load_jsonl(sp.parent / "k7.jsonl")
        yield s


def _accepted(s):
    """Accepted candidate dicts. llm_writeback writes to the CCG without
    populating summary.candidates, so fall back to ccg.json conditions."""
    cands = s.get("candidates", [])
    acc = [c for c in cands if c.get("status") == "accepted"]
    if not cands and s.get("mode") == "llm_writeback":
        ccg = s["_dir"] / "ccg.json"
        if ccg.exists():
            return list(json.loads(ccg.read_text()).get("conditions", {}).values())
    return acc


def _gt_candidate(bias, cands):
    for c in cands:
        try:
            if is_gt(bias, c):
                return c
        except Exception:
            continue
    return None


def _interventions_before_gt(s):
    """(#interventions until GT accepted, gt_accepted) from K7."""
    k7 = s["_k7"]
    bias = s["bias"]
    gt_cid = None
    for c in s.get("candidates", []):
        try:
            if is_gt(bias, c):
                gt_cid = c.get("cid")
                break
        except Exception:
            pass
    accept_step = None
    if gt_cid is not None:
        for e in k7:
            if e.get("type") == "writeback" \
                    and e["payload"].get("cid") == gt_cid \
                    and e["payload"].get("decision") in ("accepted",):
                accept_step = e.get("step")
                break
    n_before = 0
    for e in k7:
        if e.get("type") != "intervention_start":
            continue
        if accept_step is None or e.get("step", 0) <= accept_step:
            n_before += 1
    return n_before, int(accept_step is not None)


def build(runs_dir: Path = RUNS_DIR) -> dict:
    # ---- table 4 by class ----
    by = defaultdict(lambda: {"precision": [], "recall": [], "f1": [],
                              "err": [], "neff": []})
    ablation = defaultdict(lambda: {"interventions_before_gt": [],
                                    "gt_accepted": [], "verify_steps": []})
    for s in _runs(runs_dir):
        if s.get("suite") != "discovery":
            continue
        bias, mode = s["bias"], s["mode"]
        cls = bias_class(bias)
        accepted = _accepted(s)
        tp = sum(1 for c in accepted if _safe_is_gt(bias, c))
        precision = tp / len(accepted) if accepted else 0.0
        recall = 1.0 if tp else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        err = (sum(1 for c in accepted if not _safe_is_gt(bias, c)) / len(accepted)
               if accepted else 0.0)
        gt = _gt_candidate(bias, s.get("candidates", []))
        neff = min(int(gt.get("n_pos", 0)), int(gt.get("n_neg", 0))) if gt else 0
        g = by[(cls, mode)]
        g["precision"].append(precision); g["recall"].append(recall)
        g["f1"].append(f1); g["err"].append(err); g["neff"].append(neff)

        # ablation rows keyed by (bias, class, alpha)
        alpha = _alpha_of(s)
        n_before, gt_acc = _interventions_before_gt(s)
        a = ablation[(bias, cls, alpha)]
        a["interventions_before_gt"].append(n_before)
        a["gt_accepted"].append(gt_acc)
        a["verify_steps"].append(s.get("steps_used", 0))

    def _mean(xs):
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    by_class = [{"class": cls, "mode": mode, "n_runs": len(g["precision"]),
                 "precision": _mean(g["precision"]), "recall": _mean(g["recall"]),
                 "f1": _mean(g["f1"]),
                 "error_writeback_rate": _mean(g["err"]),
                 "mean_neff": _mean(g["neff"])}
                for (cls, mode), g in sorted(by.items())]
    def _ablation_sort(kv):
        bias, _cls, alpha = kv[0]
        return (bias, alpha is None, alpha if alpha is not None else 0.0)

    abl = [{"bias": bias, "class": cls, "alpha": alpha,
            "n_runs": len(a["gt_accepted"]),
            "interventions_before_gt": _mean(a["interventions_before_gt"]),
            "gt_accepted": _mean(a["gt_accepted"]),
            "verify_steps": _mean(a["verify_steps"])}
           for (bias, cls, alpha), a in sorted(ablation.items(),
                                               key=_ablation_sort)]
    return {"by_class": by_class, "ablation": abl}


def _safe_is_gt(bias, c):
    try:
        return is_gt(bias, c)
    except Exception:
        return False


def _alpha_of(s):
    """alpha for the run: cost_model event first, then summary.cost_alpha."""
    for e in s["_k7"]:
        if e.get("type") == "cost_model" and e["payload"].get("alpha") is not None:
            return float(e["payload"]["alpha"])
    a = s.get("cost_alpha")
    return float(a) if a is not None else None


def _write_csv(path: Path, rows):
    if not rows:
        print(f"(no rows for {path.name})")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default=str(RUNS_DIR))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out = Path(args.out)
    res = build(Path(args.runs))
    _write_csv(out / "table4_by_class.csv", res["by_class"])
    _write_csv(out / "table6b_cost_ablation.csv", res["ablation"])
    for row in res["by_class"]:
        print(f"  {row['class']:>22s} / {row['mode']:<14s} "
              f"P={row['precision']} R={row['recall']} F1={row['f1']} "
              f"errWB={row['error_writeback_rate']} n_eff={row['mean_neff']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
