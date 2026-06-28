"""Stage 3: K3 predicate-evaluation client (Python side).

Talks to the two mineflayer routes added in env/mineflayer/index.js:
  POST /eval_predicates  {"predicates":[{id,target,property,comparator,value}]}
  POST /state_snapshot   {}

Result semantics (contract K3): each result is
  {id, value: 0|1|None, raw, known: bool, error: str|None}
and callers MUST treat known=False as "state unavailable", never as 0.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"
_SCHEMA: Dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

REQUIRED_KEYS = ("id", "target", "property", "comparator", "value")


def schema() -> Dict[str, Any]:
    return _SCHEMA


def validate_predicate(p: Dict[str, Any]) -> Tuple[bool, str]:
    """Structural legality against the core signature (cheap, offline)."""
    for k in REQUIRED_KEYS:
        if k not in p or p[k] in (None, ""):
            return False, f"missing field '{k}'"
    prim = _SCHEMA["primitives"].get(p["target"])
    if prim is None:
        return False, f"unknown target '{p['target']}'"
    if p["comparator"] not in prim["comparators"]:
        return False, (f"comparator '{p['comparator']}' not allowed for "
                       f"{p['target']} (allowed: {prim['comparators']})")
    if prim["value_type"] == "tier_enum" and p["value"] not in _SCHEMA["tiers"]:
        return False, f"value '{p['value']}' is not a tier {_SCHEMA['tiers']}"
    if prim["value_type"] == "range" and not (
            isinstance(p["value"], (list, tuple)) and len(p["value"]) == 2):
        return False, "range value must be [a, b]"
    return True, ""


def in_whitelist(dimension: str, target: str) -> bool:
    return target in _SCHEMA["whitelist"].get(dimension, [])


def is_observe_only(target: str) -> bool:
    return target in _SCHEMA.get("observe_only", [])


# ----------------------------------------------------------------- HTTP client
def eval_predicates(env, preds: List[Dict[str, Any]],
                    timeout: int = 60) -> Dict[str, Dict[str, Any]]:
    """Evaluate a batch on the live bot; returns {id: result}. Raises on transport
    errors; per-predicate errors come back as known=False results instead."""
    for p in preds:
        ok, why = validate_predicate(p)
        if not ok:
            raise ValueError(f"illegal predicate {p.get('id')}: {why}")
    r = requests.post(f"{env.server}/eval_predicates",
                      json={"predicates": preds}, timeout=timeout)
    r.raise_for_status()
    return {res["id"]: res for res in r.json()["results"]}


def state_snapshot(env, timeout: int = 60) -> Dict[str, Any]:
    r = requests.post(f"{env.server}/state_snapshot", json={}, timeout=timeout)
    r.raise_for_status()
    return r.json()["snapshot"]
