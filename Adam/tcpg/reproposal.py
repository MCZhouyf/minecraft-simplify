"""Closed-loop tail: none-of-the-above detection + reproposal + signature fallback.

This module closes the unified loop described in the paper:

    LLM proposal distribution (high recall)            [proposer.propose_from_failure]
        -> intervention verification (select / reject) [runtime._verification_loop]
        -> ALL rejected == none-of-the-above (NOTA, a misspecification signal)
        -> counterexample-driven reproposal + signature enumeration fallback
        -> write-back                                  [ccg.write_back]

The first two stages already exist in proposer.py / runtime.py. This module adds
ONLY the tail (the parts after verification): it is pure/dependency-injected so it
runs offline (no Minecraft, no API key) and is wired into TcpgRuntime behind the
opt-in flag cfg['nota_reproposal'] (default OFF -> the frozen loop is unchanged).

Design notes
------------
* NOTA is a *clean* signal here, stronger than an observational residual: every
  proposed candidate was tested by two-sided intervention and rejected (or only
  confirmed a known recipe edge) while the action still fails -> the true gate is
  NOT in the candidate set. detect_none_of_the_above() reads candidate STATUS only.
* Counterexample reproposal feeds the rejected typed gates back to the LLM and
  asks for DIFFERENT target variables (build_counterexample_event()).
* Signature enumeration fallback (enumerate_signature_fallback()) is the structural
  analogue of proposer.expand_neighbors/expand_numeric_frontier (which walk the
  PARAMETER axis of one target): it walks the STRUCTURE axis -- other targets in
  the typed signature -- so a hidden gate is reachable even if the LLM never names
  it. It enumerates only the BOUNDED-domain targets (held_tool tiers, y_level seed,
  time_of_day day/night, sky_exposed, observed block_below); open item-/block-name
  domains (inventory_count, nearby_block, station_type, ...) stay with the LLM
  (paper Sec. 7: "categorical identity by LLM, measurable params by intervention").
  This turns the recall ceiling from "what the LLM thought of" into "what the
  signature can express" -- the latter we can guarantee to cover.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from Adam.tcpg import predicates as P
from Adam.tcpg.eventlog import log_event
from Adam.tcpg.proposer import Candidate, propose_from_failure, validate


# ----------------------------------------------------------- NOTA detection
def detect_none_of_the_above(action: str,
                             cands: Dict[str, Candidate],
                             ccg: Any) -> Optional[Dict[str, Any]]:
    """Return a misspecification signal for `action`, or None.

    Fires iff, for this action: (i) NO candidate is 'accepted' (no discovered
    gate), (ii) NO feasible candidate is still 'undecided' (the candidate set is
    exhausted -- we are not merely out of budget this trigger), and (iii) at least
    one candidate was actually decided ('rejected'/'confirmed_known') OR every
    candidate is stuck 'observe_only' (proposed but untestable). 'confirmed_known'
    does NOT count as accepted: confirming a known recipe edge while the action
    still fails means an ADDITIONAL hidden gate was not proposed.
    """
    action_cands = [c for c in cands.values() if c.action == action]
    if not action_cands:
        return None
    if any(c.status == "accepted" for c in action_cands):
        return None
    undecided_feasible = [c for c in action_cands if c.status == "undecided"]
    if undecided_feasible:
        return None  # still have things to verify -> not exhausted yet
    decided = [c for c in action_cands
               if c.status in ("rejected", "confirmed_known")]
    only_infeasible = all(c.status == "observe_only" for c in action_cands)
    if not decided and not only_infeasible:
        return None
    rejected_cids = [c.cid for c in action_cands if c.status == "rejected"]
    return {
        "action": action,
        "rejected_cids": rejected_cids,
        "n_decided": len(decided),
        "reason": ("all_candidates_infeasible" if only_infeasible
                   else "all_candidates_decided_none_accepted"),
    }


# ------------------------------------------------- counterexample reproposal
def build_counterexample_event(action: str, expected_effect: str,
                               observation: Optional[Dict[str, Any]],
                               rejected: Iterable[Candidate]) -> Dict[str, Any]:
    """Augment the failure observation with the rejected gates as counterexamples,
    so propose_from_failure asks the LLM for DIFFERENT target variables."""
    obs = dict(observation or {})
    rej = list(rejected)
    rej_text = "; ".join(
        f"{c.target}({c.property}){c.comparator}{c.value}" for c in rej) or "(none)"
    note = ("Intervention has already TESTED and REJECTED these typed gates "
            "(the action still fails even when they hold), so they do NOT gate "
            f"this action: {rej_text}. Propose DIFFERENT candidate variables "
            "(different `target`s), especially situational ones "
            "(depth/time/sky/nearby), not these. If the counterexample is a "
            "nearby liquid or environmental block such as water/lava/fire, "
            "encode it as target `nearby_block` with property equal to the "
            "block name and comparator `<=k`; do not encode nearby water as "
            "`block_below`, because `block_below` is only the exact block under "
            "the agent and is observe-only.")
    obs["feedback"] = (str(obs.get("feedback", "")) + "\n" + note).strip()
    return {"action": action, "expected_effect": expected_effect,
            "observation": obs}


# ------------------------------------------------ signature enumeration fallback
def _obs_y(obs: Dict[str, Any]) -> Optional[int]:
    pos = obs.get("position")
    y = pos.get("y") if isinstance(pos, dict) else (
        pos[1] if isinstance(pos, (list, tuple)) and len(pos) >= 2 else None)
    try:
        return None if y is None else int(round(float(y)))
    except (TypeError, ValueError):
        return None


def enumerate_signature_fallback(action: str,
                                 exclude_cids: Iterable[str],
                                 observation: Optional[Dict[str, Any]]
                                 ) -> List[Candidate]:
    """Structural siblings over the typed signature (bounded-domain targets only).

    Reads only schema.json (whitelist + ordered_domains) and the failure
    observation -- no task id, item name, or drifted constant -- so it transfers
    across environments. Each produced Candidate passes proposer.validate()."""
    schema = P.schema()
    prims = schema.get("primitives", {})
    whitelist = schema.get("whitelist", {})
    tiers = schema.get("ordered_domains", {}).get("tier_enum", [])
    obs = observation or {}
    seen = set(exclude_cids)
    out: List[Candidate] = []

    def add(dim: str, target: str, comp: str, value: Any, prop: str) -> None:
        c = Candidate(action=action, dimension=dim, target=target,
                      property=prop, comparator=comp, value=value,
                      source="signature_fallback")
        if c.cid in seen:
            return
        ok, _ = validate(c)
        if ok:
            seen.add(c.cid)
            out.append(c)

    for dim, targets in whitelist.items():
        for t in targets:
            if t not in prims or P.is_observe_only(t):
                continue
            if t == "held_tool":
                for tier in tiers:
                    add(dim, t, ">=", tier, "tier")
            elif t == "y_level":
                y = _obs_y(obs)
                if y is not None:
                    add(dim, t, "<=", int(y), "y")        # param frontier refines
            elif t == "time_of_day":
                add(dim, t, "in", [0, 12000], "time")     # day
                add(dim, t, "in", [12000, 24000], "time")  # night
            elif t == "sky_exposed":
                add(dim, t, "=", True, "sky")
                add(dim, t, "=", False, "sky")
            elif t == "block_below":
                b = obs.get("block_below")
                if b:
                    add(dim, t, "=", b, "type")
            # inventory_count / ingredient_type / held_item / nearby_block /
            # station_type / station_base_block: open item/block-name domains ->
            # left to the LLM (paper Sec. 7).
    return out


# ----------------------------------------------------------------- orchestrator
def repropose(action: str, expected_effect: str,
              observation: Optional[Dict[str, Any]],
              rejected: Iterable[Candidate],
              exclude_cids: Iterable[str],
              llm: Optional[Any] = None,
              use_signature_fallback: bool = True,
              trial_id: str = "-", step: int = -1) -> List[Candidate]:
    """Run the closed-loop tail and return NEW (not-yet-seen) candidates.

    Order: (1) counterexample-driven LLM reproposal; (2) signature enumeration
    fallback, used ONLY if (1) produced no new candidate (so it is a genuine
    last-resort coverage guarantee, not a noise source). Never raises on LLM junk.
    """
    exclude = set(exclude_cids)
    found: Dict[str, Candidate] = {}

    event = build_counterexample_event(action, expected_effect,
                                        observation, rejected)
    try:
        cands = propose_from_failure(event, llm=llm, trial_id=trial_id,
                                     step=step)
    except Exception as exc:  # noqa: BLE001
        log_event("reproposal", {"stage": "counterexample_llm",
                                 "error": str(exc)}, trial_id, step)
        cands = []
    for c in cands:
        if c.cid not in exclude:
            found[c.cid] = c
    log_event("reproposal", {"stage": "counterexample_llm",
                             "n_new": len(found),
                             "cids": list(found)}, trial_id, step)

    if use_signature_fallback and not found:
        for c in enumerate_signature_fallback(action, exclude, observation):
            if c.cid not in found:
                found[c.cid] = c
        log_event("reproposal", {"stage": "signature_fallback",
                                 "n_new": len(found),
                                 "cids": list(found)}, trial_id, step)

    return list(found.values())
