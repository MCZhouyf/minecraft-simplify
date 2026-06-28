"""Stage 4: TCPG proposer + validator (contract K4).

Two candidate generators feeding the same representation:
  * propose_from_failure(event, llm)   -- recovery direction (LLM, typed, retried)
  * candidates_from_success(...)       -- necessity direction (no LLM)

Validation is structural-only and cheap: schema legality (predicates.py) +
dimension-slot whitelist + admitted-primitive pass-through (admission.py).
The LLM is dependency-injected (`llm: Callable[[str], str]`) so offline tests
run without any API key; the default wraps Adam.infer_API.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from Adam.tcpg import predicates as P
from Adam.tcpg.eventlog import log_event

TCPG_DIR = Path(__file__).resolve().parent
REPO_ROOT = TCPG_DIR.parent.parent
PROMPT_PATH = REPO_ROOT / "prompts" / "tcpg_prompt.txt"
RUNTIME_SCHEMA_PATH = TCPG_DIR / "schema_runtime.json"

MAX_RETRIES = 2
DIMENSIONS = ("resource", "capability", "procedure", "context", "environment")
# Max numeric-frontier expansion width per trigger (see expand_numeric_frontier).
# Override via env IAP_NUMERIC_FRONTIER_K. inventory_count drifts in the suite
# are small (e.g. 4->8); y_level (C4) needs a wider window, so bump for it.
NUMERIC_FRONTIER_K = int(os.environ.get("IAP_NUMERIC_FRONTIER_K", "8"))


# ----------------------------------------------------------------- K4 dataclass
@dataclass
class Candidate:
    action: str
    dimension: str
    target: str
    property: str
    comparator: str
    value: Any
    source: str = "tcpg"            # tcpg | success_precondition
    origin: str = "core"            # core | admitted
    status: str = "undecided"       # undecided | accepted | rejected | observe_only
    n_pos: int = 0
    k_pos: int = 0
    n_neg: int = 0
    k_neg: int = 0
    created_step: int = -1
    decided_step: Optional[int] = None

    @property
    def cid(self) -> str:
        key = f"{self.action}|{self.target}|{self.property}|{self.comparator}|{self.value}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def predicate(self) -> Dict[str, Any]:
        return {"id": self.cid, "target": self.target, "property": self.property,
                "comparator": self.comparator, "value": self.value}

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cid"] = self.cid
        return d


# ----------------------------------------------------------------- validation
def admitted_names() -> set:
    """property_name set of admitted primitives (K9; empty if none yet)."""
    if not RUNTIME_SCHEMA_PATH.exists():
        return set()
    try:
        doc = json.loads(RUNTIME_SCHEMA_PATH.read_text(encoding="utf-8"))
        return {e["property_name"] for e in doc.get("admitted", [])}
    except Exception:
        return set()


# ----------------------------------------------------------------- neighbor expansion
# Signature-induced neighbour expansion. For an attribute whose signature
# value_type names an ORDERED ENUMERATED domain (schema.ordered_domains),
# the LLM's proposal often equals the *vanilla prior* value while the drifted
# environment uses an order-adjacent one. We deterministically add the direct
# predecessor/successor along the order DECLARED IN THE SIGNATURE, so that
# intervention verification can reach the true value regardless of the LLM's
# guess. This reads only the signature's type declarations — it contains no
# environment-specific constants or thresholds — so it transfers across
# environments (Sigma_MC, Sigma_ALF, ...) with zero proposer changes.
#
# Continuous numeric thresholds are intentionally NOT enumerated here: a small
# error in a numeric threshold is absorbed by the posterior's delta-margin
# decision, so no magic step size is introduced.


def _ordered_domains() -> Dict[str, list]:
    return P.schema().get("ordered_domains", {})


def _value_type_of(target: str) -> Optional[str]:
    prim = P.schema()["primitives"].get(target)
    return prim.get("value_type") if prim else None


def _numeric_meta(target: str) -> Tuple[Optional[str], bool]:
    """(monotone-direction, exact_settable) for a numeric target, read straight
    from the signature. monotone is 'up' (x>=theta resource-style gates, e.g.
    inventory_count) or 'down' (x<=theta gates, e.g. y_level); returns
    (None, False) for any target that does not declare both."""
    prim = P.schema()["primitives"].get(target) or {}
    mono = prim.get("monotone")
    return (mono if mono in ("up", "down") else None), bool(prim.get("exact_settable"))


def _observed_numeric(target: str, prop: str, obs: Dict[str, Any]) -> Optional[int]:
    """x(s_f): the value of the numeric property in the failure observation.
    inventory_count -> inventory[item] (0 if absent); y_level -> position y.
    None if the observation does not carry a readable value."""
    if target == "inventory_count":
        inv = obs.get("inventory") or {}
        try:
            return int(inv.get(prop, 0))
        except (TypeError, ValueError):
            return None
    if target == "y_level":
        pos = obs.get("position")
        y = pos.get("y") if isinstance(pos, dict) else (
            pos[1] if isinstance(pos, (list, tuple)) and len(pos) >= 2 else None)
        try:
            return None if y is None else int(round(float(y)))
        except (TypeError, ValueError):
            return None
    return None


def _numeric_frontier_for(c: Candidate, obs: Dict[str, Any], k: int) -> List[Candidate]:
    """Signature-induced numeric-frontier siblings for a monotone numeric
    threshold candidate, anchored on the failure value v = x(s_f).

    Boundary evidence: the action failed at x=v, so for an 'up' gate the true
    minimum threshold (if any) lies ABOVE v -> propose x>=n for n in {v+1..v+k};
    for a 'down' gate it lies BELOW v -> propose x<=n for n in {v-1..v-k}.
    Reads only the signature's numeric type, monotonicity and exact-settable
    flag plus the observed v -- no task id, item name, or drifted constant.
    Returns [] for non-(exact-settable monotone numeric) targets or unknown v."""
    mono, exact = _numeric_meta(c.target)
    if not mono or not exact or _value_type_of(c.target) not in ("int", "number"):
        return []
    v = _observed_numeric(c.target, c.property, obs)
    if v is None:
        return []
    comp, vals = ((">=", [v + i for i in range(1, k + 1)]) if mono == "up"
                  else ("<=", [v - i for i in range(1, k + 1)]))
    return [Candidate(action=c.action, dimension=c.dimension, target=c.target,
                      property=c.property, comparator=comp, value=n,
                      source="frontier", origin=c.origin) for n in vals]


def expand_numeric_frontier(cands: List[Candidate], obs: Dict[str, Any],
                            k: int = NUMERIC_FRONTIER_K) -> List[Candidate]:
    """Augment cands with numeric-frontier siblings (deduped by cid; only
    siblings that pass validate()). The numeric analogue of expand_neighbors:
    where expand_neighbors walks an ordered ENUM (tool tier), this walks the
    monotone numeric frontier located by the failure observation. Recall-only --
    it never alters the dual-pool accept/reject standard."""
    by_cid = {c.cid: c for c in cands}
    for c in list(cands):
        for sib in _numeric_frontier_for(c, obs, k):
            if sib.cid in by_cid:
                continue
            ok, _ = validate(sib)
            if ok:
                by_cid[sib.cid] = sib
    return list(by_cid.values())


def _time_complement_for(c: Candidate) -> List[Candidate]:
    if c.target != "time_of_day" or c.comparator != "in":
        return []
    if not isinstance(c.value, (list, tuple)) or len(c.value) != 2:
        return []
    try:
        raw_a, raw_b = [int(x) for x in c.value]
        a, b = sorted(x % 24000 for x in (raw_a, raw_b))
    except (TypeError, ValueError):
        return []
    if abs(raw_b - raw_a) >= 24000:
        return [
            Candidate(action=c.action, dimension=c.dimension, target=c.target,
                      property=c.property, comparator="in", value=[0, 12000],
                      source="frontier", origin=c.origin),
            Candidate(action=c.action, dimension=c.dimension, target=c.target,
                      property=c.property, comparator="in", value=[12000, 24000],
                      source="frontier", origin=c.origin),
        ]
    # Only add the single complementary half-day window. General interval
    # complements are two disjoint ranges and do not fit the current predicate
    # shape; adding them here would change semantics instead of just recall.
    if (a, b) == (0, 12000):
        comp = [12000, 24000]
    elif (a, b) == (12000, 24000):
        comp = [0, 12000]
    else:
        return []
    return [Candidate(action=c.action, dimension=c.dimension, target=c.target,
                      property=c.property, comparator="in", value=comp,
                      source="frontier", origin=c.origin)]


def expand_time_complements(cands: List[Candidate]) -> List[Candidate]:
    by_cid = {c.cid: c for c in cands}
    for c in list(cands):
        for sib in _time_complement_for(c):
            if sib.cid in by_cid:
                continue
            ok, _ = validate(sib)
            if ok:
                by_cid[sib.cid] = sib
    return list(by_cid.values())


def normalize_candidate(c: Candidate) -> Candidate:
    """Normalize common LLM aliases into schema-valid predicate values."""
    if c.target != "time_of_day" or c.comparator != "in":
        return c
    if not isinstance(c.value, str):
        return c
    token = c.value.strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"day", "daytime", "morning", "noon"}:
        return Candidate(action=c.action, dimension=c.dimension,
                         target=c.target, property=c.property,
                         comparator=c.comparator, value=[0, 12000],
                         source=c.source, origin=c.origin)
    if token in {"night", "nighttime", "night_time", "midnight"}:
        return Candidate(action=c.action, dimension=c.dimension,
                         target=c.target, property=c.property,
                         comparator=c.comparator, value=[12000, 24000],
                         source=c.source, origin=c.origin)
    return c


def _neighbors_for(c: Candidate) -> List[Candidate]:
    """Order-adjacent sibling candidates for an ordered-ENUM predicate, using
    the order declared in the signature. Returns [] for any attribute whose
    value_type is not a declared ordered domain (incl. all numeric thresholds)."""
    domains = _ordered_domains()
    vt = _value_type_of(c.target)
    order = domains.get(vt) if vt else None
    if not order or str(c.value) not in order:
        return []
    i = order.index(str(c.value))
    sibs: List[Candidate] = []
    for j in (i - 1, i + 1):
        if 0 <= j < len(order):
            sibs.append(Candidate(action=c.action, dimension=c.dimension,
                                   target=c.target, property=c.property,
                                   comparator=c.comparator, value=order[j],
                                   source="neighbor", origin=c.origin))
    return sibs


def expand_neighbors(cands: List[Candidate]) -> List[Candidate]:
    """Return cands augmented with signature-induced order-adjacent siblings
    (deduped by cid, only siblings that pass validate())."""
    by_cid = {c.cid: c for c in cands}
    for c in list(cands):
        for sib in _neighbors_for(c):
            if sib.cid in by_cid:
                continue
            ok, _ = validate(sib)
            if ok:
                by_cid[sib.cid] = sib
    return list(by_cid.values())


def validate(c: Candidate) -> Tuple[bool, str]:
    if c.dimension not in DIMENSIONS:
        return False, f"unknown dimension '{c.dimension}'"
    if c.target in admitted_names():
        # admitted primitives are boolean expressions: <name> = true/false
        if c.comparator != "=" or not isinstance(c.value, bool):
            return False, "admitted primitive expects comparator '=' with boolean value"
        return True, ""
    if not P.in_whitelist(c.dimension, c.target):
        return False, (f"target '{c.target}' not whitelisted for dimension "
                       f"'{c.dimension}'")
    return P.validate_predicate(c.predicate())


# ----------------------------------------------------------------- prompt I/O
def render_whitelist() -> str:
    s = P.schema()
    lines = []
    for dim, targets in s["whitelist"].items():
        items = ", ".join(
            f"{t}(comparators: {'/'.join(s['primitives'][t]['comparators'])})"
            for t in targets)
        lines.append(f"  {dim}: {items}")
    return "\n".join(lines)


_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?|```")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_MISSING_COLON_RE = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*"\s+)(?=["{\[\-0-9tfn])')


def _loads_llm_json_array(src: str) -> List[Dict[str, Any]]:
    """Parse the candidate array, with a narrow repair pass for common LLM slips.

    This intentionally stays conservative: it only fixes JSON-adjacent syntax
    errors and still requires the top-level value to be a list. Semantic legality
    remains entirely in validate().
    """
    try:
        arr = json.loads(src)
    except json.JSONDecodeError:
        repaired = src
        repaired = re.sub(r"\bTrue\b", "true", repaired)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)
        repaired = _MISSING_COLON_RE.sub(r"\1: ", repaired)
        arr = json.loads(repaired)
    if not isinstance(arr, list):
        raise ValueError("top-level JSON is not an array")
    return arr


def parse_json_array(text: str) -> List[Dict[str, Any]]:
    """Tolerant extraction of the first JSON array in an LLM reply."""
    cleaned = _FENCE_RE.sub("", text or "")
    start = cleaned.find("[")
    if start < 0:
        raise ValueError("no JSON array found in reply")
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "[":
            depth += 1
        elif cleaned[i] == "]":
            depth -= 1
            if depth == 0:
                return _loads_llm_json_array(cleaned[start:i + 1])
    raise ValueError("unbalanced JSON array in reply")


def _default_llm() -> Callable[[str], str]:
    from Adam.infer_API import get_response
    model = os.environ.get("IAP_LLM_MODEL", "gpt-4o")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    return lambda prompt: get_response(prompt=prompt, model_name=model,
                                       api_key=api_key, base_url=base_url)


def _build_prompt(event: Dict[str, Any]) -> str:
    obs = event.get("observation", {})
    return PROMPT_PATH.read_text(encoding="utf-8").format(
        action=event["action"],
        expected_effect=event.get("expected_effect", ""),
        inventory=json.dumps(obs.get("inventory", {}), ensure_ascii=False),
        held=obs.get("held", "empty"),
        position=obs.get("position", None),
        time=obs.get("time", None),
        nearby_blocks=obs.get("nearby_blocks", []),
        feedback=obs.get("feedback", ""),
        whitelist=render_whitelist(),
    )


# ----------------------------------------------------------------- generators
def propose_from_failure(event: Dict[str, Any],
                         llm: Optional[Callable[[str], str]] = None,
                         max_retries: int = MAX_RETRIES,
                         expand: bool = True,
                         trial_id: str = "-", step: int = -1) -> List[Candidate]:
    """Recovery-direction candidates. Illegal items trigger up to `max_retries`
    regenerations (with the validator's reasons appended); legal candidates are
    deduplicated by cid. With expand=True, ordered-domain candidates (tool tier,
    counts, y-level) are augmented with order-adjacent siblings so intervention
    verification covers the true threshold even when the LLM proposes only its
    vanilla prior. Returns possibly-empty list; never raises on LLM junk."""
    llm = llm or _default_llm()
    prompt = _build_prompt(event)
    legal: Dict[str, Candidate] = {}
    first_pass = {"total": 0, "legal": 0}

    feedback_suffix = ""
    for attempt in range(max_retries + 1):
        try:
            reply = llm(prompt + feedback_suffix)
            items = parse_json_array(reply)
        except Exception as exc:
            log_event("proposal", {"trigger": "failure", "attempt": attempt,
                                   "error": str(exc)}, trial_id, step)
            feedback_suffix = ("\n\nYour previous reply was not a parseable JSON "
                               f"array ({exc}). Output ONLY the JSON array first.")
            continue

        errors: List[str] = []
        for raw in items:
            if attempt == 0:
                first_pass["total"] += 1
            try:
                c = Candidate(action=event["action"],
                              dimension=raw["dimension"], target=raw["target"],
                              property=str(raw["property"]),
                              comparator=raw["comparator"], value=raw["value"])
                c = normalize_candidate(c)
            except (KeyError, TypeError) as exc:
                errors.append(f"{raw}: missing field {exc}")
                continue
            ok, why = validate(c)
            log_event("validate", {"cid": c.cid, "target": c.target,
                                   "property": c.property, "ok": ok,
                                   "reason": why}, trial_id, step)
            if ok:
                if attempt == 0:
                    first_pass["legal"] += 1
                legal[c.cid] = c
            else:
                errors.append(f"{raw}: {why}")
                try:
                    from Adam.tcpg.admission import record_rejection
                    record_rejection(str(raw.get("target")), str(raw.get("property")))
                except Exception:
                    pass

        log_event("proposal", {"trigger": "failure", "attempt": attempt,
                               "n_items": len(items), "n_legal": len(legal),
                               "first_pass": first_pass}, trial_id, step)
        if not errors:
            break
        feedback_suffix = ("\n\nThese previous candidates were ILLEGAL — fix or "
                           "replace ONLY them, keep the same output format:\n- "
                           + "\n- ".join(errors[:6]))
    result = list(legal.values())
    if expand:
        before = len(result)
        result = expand_neighbors(result)
        if len(result) > before:
            log_event("neighbor_expand",
                      {"added": len(result) - before,
                       "cids": [c.cid for c in result]}, trial_id, step)
        before_f = len(result)
        result = expand_numeric_frontier(result, event.get("observation", {}))
        if len(result) > before_f:
            log_event("frontier_expand",
                      {"added": len(result) - before_f,
                       "cids": [c.cid for c in result]}, trial_id, step)
        before_t = len(result)
        result = expand_time_complements(result)
        if len(result) > before_t:
            log_event("frontier_expand",
                      {"added": len(result) - before_t,
                       "cids": [c.cid for c in result]}, trial_id, step)
    return result


def candidates_from_success(action: str,
                            assumed_preconds: Iterable[Dict[str, Any]],
                            skip_cids: Iterable[str] = (),
                            trial_id: str = "-", step: int = -1) -> List[Candidate]:
    """Necessity-direction candidates: planner-assumed, currently-satisfied,
    not-yet-decided preconditions of a SUCCESSFUL action. No LLM involved."""
    skip = set(skip_cids)
    out: List[Candidate] = []
    for pre in assumed_preconds:
        c = Candidate(action=action, dimension=pre["dimension"],
                      target=pre["target"], property=str(pre["property"]),
                      comparator=pre["comparator"], value=pre["value"],
                      source="success_precondition")
        if c.cid in skip:
            continue
        ok, why = validate(c)
        log_event("validate", {"cid": c.cid, "target": c.target, "ok": ok,
                               "reason": why, "source": "success"}, trial_id, step)
        if ok:
            out.append(c)
    return out
