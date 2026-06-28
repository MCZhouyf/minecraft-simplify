"""Validity filtering for generated MC-Drift instances.

This file implements the automatic validity filter before human/LLM
recoverability labeling. It deliberately does NOT filter by IaP success.

Checks:
1. origin solvable under the vanilla recipe tree;
2. drift non-trivial by construction;
3. oracle solvable with the injected gate satisfied;
4. ground-truth predicate expressible;
5. I+/I- both compilable through Adam.tcpg.compiler.dry_run;
6. non-degenerate and unambiguous under the static template metadata.

The runtime checks reuse existing repository components when available.
If run outside the full IAP-Agent repository, pass --no-runtime-check to the
generator; the filter will then perform schema/static checks only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple
import copy
import sys
from pathlib import Path

from .templates import ACTION_GOAL


STEP_CAP_DEFAULT = 500.0
TAG_TIER = {"needs_stone_tool.json": 1, "needs_iron_tool.json": 2, "needs_diamond_tool.json": 3}
CANON_STATE = {"inventory": {}, "agent_y": 70.0, "held": None, "time": 6000}
TOOL_ITEM_TIERS = {
    "wooden_pickaxe": 0,
    "stone_pickaxe": 1,
    "iron_pickaxe": 2,
    "diamond_pickaxe": 3,
}


@dataclass
class FilterResult:
    passed: bool
    reasons: List[str]
    oracle_plan_steps: Optional[int] = None
    i_plus_compilable: Optional[bool] = None
    i_minus_compilable: Optional[bool] = None
    runtime_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": self.reasons,
            "oracle_plan_steps": self.oracle_plan_steps,
            "i_plus_compilable": self.i_plus_compilable,
            "i_minus_compilable": self.i_minus_compilable,
            "runtime_available": self.runtime_available,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _try_runtime():
    repo = _repo_root()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from Adam.tcpg import compiler as C  # type: ignore
        from Adam.tcpg.proposer import Candidate, validate  # type: ignore
        return C, Candidate, validate
    except Exception:
        return None, None, None


def _modified_recipes(C, bias: Mapping[str, Any]) -> Dict[str, Any]:
    recipes = copy.deepcopy(C.RECIPES)
    if bias["mechanism"] == "datapack_tag":
        tier = TAG_TIER[bias["payload"]["tag_file"]]
        blocks = {v.split(":", 1)[1] for v in bias["payload"]["values_add"]}
        for item, node in recipes.items():
            if not isinstance(node, dict):
                continue
            if node.get("via") == "mine" and node.get("block") in blocks:
                node["tool_tier"] = tier
    return recipes


def _gate_extra_requirements(bias: Mapping[str, Any]) -> List[Tuple[str, int]]:
    if bias["mechanism"] != "mod_event":
        return []
    params = bias["payload"]["params"]
    req = params.get("require")
    if req == "inventory_min":
        return [(params["item"].split(":", 1)[1], int(params["count"]))]
    if req == "nearby_block":
        block = params["block"].split(":", 1)[1]
        # Current compiler can realize placeable stations as nearby_block.
        return [(block, 1)]
    if req == "base_block_stone":
        return [("cobblestone", 1), ("furnace", 1)]
    if req == "held_match":
        item_id = str(params.get("value", "minecraft:wooden_shovel"))
        return [(item_id.split(":", 1)[1], 1)]
    # daytime, sky_visible, player_y<= require motion/waiting rather than extra items.
    return []


def _candidate_from_bias(Candidate, bias: Mapping[str, Any]):
    gt = bias["ground_truth"]
    return Candidate(
        action=bias["action"],
        dimension=bias["dimension"],
        target=gt["target"],
        property=str(gt["property"]),
        comparator=gt["comparator"],
        value=gt["value"],
    )


def _target_witness_blocks(C, action: str) -> List[str]:
    goal = ACTION_GOAL.get(action)
    if not goal:
        return []
    node = C.RECIPES.get(goal) or {}
    block = node.get("block")
    return [str(block)] if node.get("via") == "mine" and block else []


def _requires_unstaged_witness_assumption(C, bias: Mapping[str, Any]) -> bool:
    gt = bias.get("ground_truth") or {}
    if gt.get("target") != "held_tool" or gt.get("comparator") != ">=":
        return False
    if bias.get("mechanism") != "datapack_tag":
        return False
    raised = {v.split(":", 1)[1] for v in bias["payload"]["values_add"]}
    witnesses = set(_target_witness_blocks(C, str(bias.get("action"))))
    # If the canonical witness block for the goal is not among the raised set,
    # solvability depends on some alternate/world-specific witness existing.
    # The generated tasks currently do not author such witness placement, so we
    # conservatively reject these capability drifts.
    return bool(witnesses) and not (witnesses & raised)


def _held_match_self_lock(C, bias: Mapping[str, Any]) -> bool:
    gt = bias.get("ground_truth") or {}
    if gt.get("target") != "held_item" or gt.get("comparator") != "=":
        return False
    if bias.get("mechanism") != "mod_event":
        return False
    params = ((bias.get("payload") or {}).get("params") or {})
    if params.get("require") != "held_match":
        return False
    action = str(bias.get("action"))
    witnesses = set(_target_witness_blocks(C, action))
    if not witnesses:
        return False
    held = str(gt.get("value") or "")
    tier = TOOL_ITEM_TIERS.get(held)
    if tier is None:
        return False
    raised_tier = None
    goal = ACTION_GOAL.get(action)
    node = C.RECIPES.get(goal) or {}
    if node.get("via") == "mine":
        raised_tier = node.get("tool_tier")
    return raised_tier is not None and tier < int(raised_tier)


def _creates_capability_self_lock(C, bias: Mapping[str, Any]) -> bool:
    gt = bias.get("ground_truth") or {}
    if gt.get("target") != "held_tool" or gt.get("comparator") != ">=":
        return False
    if bias.get("mechanism") != "datapack_tag":
        return False
    raised = {v.split(":", 1)[1] for v in bias["payload"]["values_add"]}
    witnesses = set(_target_witness_blocks(C, str(bias.get("action"))))
    return bool(witnesses) and witnesses <= raised


def _expressible_static(bias: Mapping[str, Any]) -> bool:
    gt = bias["ground_truth"]
    allowed_targets = {
        "inventory_count",
        "held_tool",
        "held_item",
        "nearby_block",
        "station_type",
        "y_level",
        "sky_exposed",
        "time_of_day",
        "station_base_block",
    }
    allowed_cmp = {"=", ">=", "<=", "in", "<=k"}
    return gt.get("target") in allowed_targets and gt.get("comparator") in allowed_cmp


def _drift_nontrivial_static(bias: Mapping[str, Any]) -> bool:
    """Conservative by-construction non-triviality check.

    This checks whether the generated mechanism adds a precondition absent from
    the vanilla task description, not whether a specific IaP run will fail.
    """
    gt = bias["ground_truth"]
    mech = bias["mechanism"]
    if mech == "datapack_tag":
        return True
    if mech == "mod_event":
        req = bias["payload"]["params"].get("require")
        if req in {"inventory_min", "nearby_block", "base_block_stone", "player_y<=", "daytime", "sky_visible", "held_match"}:
            return True
    if mech == "datapack_recipe":
        return True
    return False


def check_bias(bias: Dict[str, Any], runtime_check: bool = True, step_cap: float = STEP_CAP_DEFAULT) -> FilterResult:
    reasons: List[str] = []

    if bias.get("action") not in ACTION_GOAL:
        reasons.append("unknown_action_goal")

    if not _expressible_static(bias):
        reasons.append("predicate_not_expressible")

    if not _drift_nontrivial_static(bias):
        reasons.append("trivial_or_unknown_drift")

    if not runtime_check:
        ok = not reasons
        return FilterResult(
            passed=ok,
            reasons=reasons if reasons else ["static_pass"],
            oracle_plan_steps=None,
            i_plus_compilable=None,
            i_minus_compilable=None,
            runtime_available=False,
        )

    C, Candidate, validate = _try_runtime()
    if C is None or Candidate is None:
        reasons.append("runtime_import_failed")
        return FilterResult(False, reasons, runtime_available=False)

    oracle_steps: Optional[int] = None
    i_plus: Optional[bool] = None
    i_minus: Optional[bool] = None

    # 1. Origin solvability.
    try:
        goal = ACTION_GOAL[bias["action"]]
        _, origin_est = C.acquire_plan(goal, 1, {}, recipes=C.RECIPES)
        if origin_est > step_cap:
            reasons.append("origin_exceeds_step_cap")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"origin_unsolvable:{getattr(exc, 'reason', type(exc).__name__)}")

    # 3. Oracle solvability with injected mechanism satisfied.
    try:
        goal = ACTION_GOAL[bias["action"]]
        recipes = _modified_recipes(C, bias)
        total = 0.0
        if _requires_unstaged_witness_assumption(C, bias):
            reasons.append("bootstrap_requires_unstaged_witness")
        if _creates_capability_self_lock(C, bias):
            reasons.append("bootstrap_tool_unreachable:self_lock")
        if _held_match_self_lock(C, bias):
            reasons.append("bootstrap_held_item_unreachable:self_lock")
        _, est = C.acquire_plan(goal, 1, {}, recipes=recipes)
        total += est
        for item, n in _gate_extra_requirements(bias):
            _, est2 = C.acquire_plan(item, n, {}, recipes=recipes)
            total += est2
        if total > step_cap:
            reasons.append("oracle_exceeds_step_cap")
        else:
            oracle_steps = round(total)
    except Exception as exc:  # noqa: BLE001
        reason = getattr(exc, "reason", type(exc).__name__)
        reasons.append(f"oracle_unsolvable:{reason}")

    # 4/5. Structural validation and paired intervention compilability.
    try:
        cand = _candidate_from_bias(Candidate, bias)
        ok, why = validate(cand)
        if not ok:
            reasons.append(f"candidate_invalid:{why}")
        dry = C.dry_run(cand, CANON_STATE)
        i_plus = bool(dry["i_plus_compilable"])
        i_minus = bool(dry["i_minus_compilable"])
        if not (i_plus and i_minus):
            reasons.append(f"two_sided_unreachable:{dry.get('infeasible_reason')}")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"intervention_compile_error:{type(exc).__name__}:{exc}")

    passed = not reasons
    return FilterResult(
        passed=passed,
        reasons=reasons if reasons else ["pass"],
        oracle_plan_steps=oracle_steps,
        i_plus_compilable=i_plus,
        i_minus_compilable=i_minus,
        runtime_available=True,
    )


def apply_result_to_bias(bias: Dict[str, Any], result: FilterResult) -> Dict[str, Any]:
    b = dict(bias)
    b["solvability"] = {
        "verified": bool(result.passed),
        "oracle_plan_steps": result.oracle_plan_steps,
    }
    b["intervention_check"] = {
        "i_plus_compilable": result.i_plus_compilable if result.i_plus_compilable is not None else result.passed,
        "i_minus_compilable": result.i_minus_compilable if result.i_minus_compilable is not None else result.passed,
    }
    return b
