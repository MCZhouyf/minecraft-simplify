"""Post-run data sanity check (round-2 plan B: replaces the standalone
injection probe). Instead of constructing unsat/sat states up-front (which was
fragile and self-invalidating), this verifies injection validity and data
quality directly from the REAL run data:

  INJECTION VALIDITY (probe replacement, BLOCKING)
  - A discovery bias whose tcpg natural observations ALL succeed never produced
    a failure trigger -> the injection is ineffective. Flagged as BLOCKING; such
    a bias's data must be discarded and the injection channel re-checked
    (manual sec.2 step 1).

  DATA QUALITY
  - empty k7 / short episode count
  - tcpg run with 0 interventions
  - tcpg discovery run that did not accept the GT predicate (cause tristate:
    written-back / in-pool-undecided / never-proposed)
  - llm_writeback run with 0 writeback events (ablation floor must write back)
  - k7 event completeness for tcpg
  - trigger_abort / aborted (transactional execution) stats
  - context pollution traces (held=null carried forward)

Round-3 fixes (stage 3):
  * GT_TARGET is derived from biases.yaml ground_truth (was a stale hand-kept
    map: C1/C3 pointed at held_tool, and R4/R5/R6/C4 were missing entirely).
  * K7 payloads are nested under e["payload"]; the round-2 reader looked at
    e["ctx_snapshot"] / e["payload"]["target"] (proposal/neighbor_expand carry
    no "target"), so the pollution and proposed-target checks were dead. The
    proposed/in-pool target set now comes from summary["candidates"] (each
    candidate carries target/property/comparator/value), and ctx_snapshot is
    read from the payload. GT acceptance reuses evaluate.is_gt for a full
    predicate match (so held_tool>=iron is not mistaken for the held_tool>=
    diamond true cause in R6).

Usage: python3 experiments/sanity_check.py
Exit code 0 if no blocking issue, 1 if any injection-invalid bias is found.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import yaml                                                  # noqa: E402

RUNS = REPO / "experiments" / "runs"
_BIASES = yaml.safe_load(
    (REPO / "mc_drift" / "biases" / "biases.yaml").read_text())["biases"]
# Single source of truth for the GT target per bias (manual sec.0 table).
GT_TARGET = {b["id"]: b["ground_truth"]["target"] for b in _BIASES}

# Full-predicate GT match (target+property+comparator+value, with y_level/time
# tolerance) reused from the evaluator so this gate and the tables agree.
try:
    from experiments.evaluate import is_gt as _is_gt        # noqa: E402
except Exception:                                            # pragma: no cover
    _is_gt = None

TCPG_REQUIRED_EVENTS = {"proposal", "compile", "intervention_start",
                        "retry", "undo", "posterior_update"}


def load_jsonl(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _gt_hit(bias, cand):
    """True if candidate matches the GT predicate of `bias`."""
    if _is_gt is not None and bias in GT_TARGET:
        try:
            return _is_gt(bias, cand)
        except Exception:
            pass
    return cand.get("target") == GT_TARGET.get(bias)        # target-only fallback


def main():
    blocking, warns, ok = [], [], 0
    invalid_injection = []          # biases whose injection looks ineffective
    for d in sorted(RUNS.rglob("summary.json")):
        s = json.loads(d.read_text())
        r = s["run_id"]
        bias = s.get("bias")
        mode = s.get("mode")
        suite = s.get("suite")
        k7 = load_jsonl(d.parent / "k7.jsonl")
        eps = load_jsonl(d.parent / "episodes.jsonl")
        types = [e.get("type") for e in k7]
        cands = s.get("candidates", [])

        # ---- data quality ----
        if not k7:
            warns.append(f"{r}: EMPTY k7")
        if len(eps) < s.get("episodes", 6):
            warns.append(f"{r}: only {len(eps)} episodes (<{s.get('episodes', 6)})")
        if mode == "tcpg" and types.count("intervention_start") == 0:
            warns.append(f"{r}: tcpg with 0 interventions")
        if mode == "tcpg" and TCPG_REQUIRED_EVENTS - set(types):
            warns.append(f"{r}: tcpg missing k7 events "
                         f"{sorted(TCPG_REQUIRED_EVENTS - set(types))}")
        if mode == "llm_writeback":
            if types.count("writeback") == 0:
                warns.append(f"{r}: llm_writeback with 0 writeback events "
                             f"(ablation floor empty)")

        # ---- INJECTION VALIDITY (probe replacement, BLOCKING) ----
        # Only meaningful for discovery tcpg runs (oracle/writeback modes may
        # legitimately succeed). If every natural observation succeeded, the
        # injected drift never blocked the action -> ineffective injection.
        if suite == "discovery" and mode == "tcpg" and eps:
            nat = [e.get("natural_success") for e in eps]
            if all(bool(v) for v in nat):
                invalid_injection.append(bias)
                blocking.append(
                    f"{r}: INJECTION INVALID -- all {len(nat)} natural "
                    f"observations succeeded; drift never triggered a failure. "
                    f"Discard {bias} data and re-check the injection channel "
                    f"(datapack toggle / mod config).")

        # ---- cause tristate (GT discovery) ----
        if mode == "tcpg" and suite == "discovery":
            gt = GT_TARGET.get(bias)
            accepted = [c for c in cands if c.get("status") == "accepted"]
            gt_written = any(_gt_hit(bias, c) for c in accepted)
            # all candidates that reached the pool carry their target; a strict
            # predicate hit anywhere in the pool means "proposed & in pool".
            gt_in_pool = any(_gt_hit(bias, c) for c in cands)
            # softer signal: the GT *target* showed up (e.g. held_tool>=iron for
            # an R6 whose true value is diamond -> expansion should have added it)
            target_seen = any(c.get("target") == gt for c in cands)
            if gt_written:
                pass                                  # success
            elif gt_in_pool:
                warns.append(f"{r}: GT predicate in pool but UNDECIDED "
                             f"(weak signal; expected for some biases)")
            elif target_seen:
                warns.append(f"{r}: GT target '{gt}' proposed but the true VALUE "
                             f"never reached an accept (check neighbor expansion)")
            else:
                warns.append(f"{r}: GT '{gt}' NEVER proposed "
                             f"(proposal-recall limit, e.g. numeric boundary)")

        # ---- transactional execution stats ----
        n_abort = types.count("trigger_abort")
        if n_abort:
            reasons = [e.get("payload", {}).get("reason")
                       for e in k7 if e.get("type") == "trigger_abort"]
            warns.append(f"{r}: {n_abort} trigger_abort (transactional reset) "
                         f"{[x for x in reasons if x]}")
        if s.get("aborted"):
            warns.append(f"{r}: run ABORTED at time budget "
                         f"({s.get('wall_seconds')}s / {s.get('time_budget_s')}s)")

        # ---- context pollution detection (payload-nested) ----
        for e in k7:
            if e.get("type") == "intervention_start":
                snap = e.get("payload", {}).get("ctx_snapshot", {}) or {}
                if "held.name" in snap and snap.get("held.name") is None:
                    warns.append(f"{r}: intervention_start with held=null "
                                 f"(possible context pollution)")
                    break

        ok += 1

    # ---- report ----
    print(f"checked {ok} runs | {len(blocking)} BLOCKING | {len(warns)} warnings")
    for b in blocking:
        print("BLOCK", b)
    for w in warns:
        print("WARN ", w)
    if invalid_injection:
        print(f"\n!! INJECTION-INVALID biases (discard + re-check channel): "
              f"{sorted(set(invalid_injection))}")
        print("   These never triggered a failure; their data is contaminated.")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
