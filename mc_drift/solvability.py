"""Stage 5: dataset solvability verifier (INV-1, relative to the agent's
primitive action layer).

For every bias we build the POST-INJECTION mechanism model (a copy of the
recipe tree / tier table modified per K1) and verify that the bias's action
goal is still reachable with the bias condition SATISFIED via the agent's
own primitives, within the step cap. We also dry-run-compile the ground-truth
candidate's I+/I- (intervention_check backfill).

Outputs:
  * mc_drift/out/solvability_report.json   (always)
  * --write : backfills solvability/intervention_check lines in biases.yaml
              via targeted line replacement (comments preserved)

The deliberately-illegal circular bias ("diamond ore needs a diamond pickaxe")
is checked to be REJECTED — evidence the constructor is non-vacuous (E.3).
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg import compiler as C                       # noqa: E402
from Adam.tcpg.proposer import Candidate                  # noqa: E402
from mc_drift.datapack_gen import BIASES_PATH, load_biases  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "out" / "solvability_report.json"
STEP_CAP = 500.0

ACTION_GOAL = {
    "craftFurnace": "furnace", "craftIronPickaxe": "iron_pickaxe",
    "craftFence": "oak_fence", "craftBoat": "oak_boat", "craftPlanks": "oak_planks",
    "mineIronOre": "raw_iron", "mineGoldOre": "raw_gold",
    "gatherCoalOre": "coal", "mineDiamondOre": "diamond",
    "gatherSand": "sand", "smeltRawIron": "iron_ingot",
    "smeltRawGold": "gold_ingot",
}
TAG_TIER = {"needs_stone_tool.json": 1, "needs_iron_tool.json": 2,
            "needs_diamond_tool.json": 3}
# canonical fresh-spawn state for intervention dry runs
CANON_STATE = {"inventory": {}, "agent_y": 70.0, "held": None, "time": 6000}


def modified_recipes(bias: Dict[str, Any]) -> Dict[str, Any]:
    recipes = copy.deepcopy(C.RECIPES)
    if bias["mechanism"] == "datapack_tag":
        tier = TAG_TIER[bias["payload"]["tag_file"]]
        # EXACT block-name match only: C1 tags ONLY deepslate_iron_ore, so the
        # shallow iron_ore path stays open — that asymmetry IS the solvability
        # argument for capability biases (deep variant gated, shallow witness).
        blocks = {v.split(":", 1)[1] for v in bias["payload"]["values_add"]}
        for item, node in recipes.items():
            if not isinstance(node, dict):
                continue
            if node.get("via") == "mine" and node.get("block") in blocks:
                node["tool_tier"] = tier
    return recipes


def gate_extra_requirements(bias: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Items the agent must additionally be able to ACQUIRE for the positive
    case of a mod_event gate (waiting/moving gates need no extra items)."""
    if bias["mechanism"] != "mod_event":
        return []
    params = bias["payload"]["params"]
    req = params.get("require")
    if req == "inventory_min":
        return [(params["item"].split(":", 1)[1], int(params["count"]))]
    if req == "nearby_block":
        blk = params["block"].split(":", 1)[1]
        # Natural fluids are reached by moving to an existing body, not by
        # acquiring/placing the fluid as an inventory item.
        if blk in ("water", "lava", "flowing_water", "flowing_lava"):
            return []
        return [(blk, 1)]
    if req == "base_block_stone":
        return [("cobblestone", 1), ("furnace", 1)]
    if req == "held_match":
        return [(params["value"].split(":", 1)[1], 1)]
    return []      # daytime / sky_visible / player_y<= : wait or move, no items


def check_bias(bias: Dict[str, Any]) -> Dict[str, Any]:
    goal = ACTION_GOAL[bias["action"]]
    recipes = modified_recipes(bias)
    total = 0.0
    try:
        steps, est = C.acquire_plan(goal, 1, {}, recipes=recipes)
        total += est
        for item, n in gate_extra_requirements(bias):
            _, e2 = C.acquire_plan(item, n, {}, recipes=recipes)
            total += e2
        verified = total <= STEP_CAP
        reason = None if verified else "exceeds_step_cap"
    except C.Infeasible as exc:
        verified, total, reason = False, 0.0, exc.reason

    gt = bias["ground_truth"]
    cand = Candidate(action=bias["action"], dimension=bias["dimension"],
                     target=gt["target"], property=str(gt["property"]),
                     comparator=gt["comparator"], value=gt["value"])
    dr = C.dry_run(cand, CANON_STATE)
    return {"id": bias["id"], "verified": verified,
            "oracle_plan_steps": (round(total) if verified else None),
            "reject_reason": reason,
            "i_plus_compilable": dr["i_plus_compilable"],
            "i_minus_compilable": dr["i_minus_compilable"],
            "intervention_infeasible_reason": dr["infeasible_reason"]}


def circular_bias_rejected() -> bool:
    """E.3 sanity: 'diamond ore needs a DIAMOND pickaxe' must be unsolvable."""
    recipes = copy.deepcopy(C.RECIPES)
    recipes["diamond"]["tool_tier"] = 3
    try:
        C.acquire_plan("diamond", 1, {}, recipes=recipes)
        return False
    except C.Infeasible as exc:
        return exc.reason == "recipe_unreachable"


def _set_block_field(block: str, key: str, value: str, indent: str = "    ") -> str:
    """Set `key: value` inside a single bias block, supporting both block style
    (`key:` possibly with a value on the same line) and absence (no-op).
    Replaces only the FIRST occurrence at the given indent."""
    pat = re.compile(rf"(?m)^{re.escape(indent)}{re.escape(key)}:.*$")
    repl = f"{indent}{key}: {value}"
    if pat.search(block):
        return pat.sub(lambda _m: repl, block, count=1)
    return block


def backfill_yaml(results: List[Dict[str, Any]], path: Path = BIASES_PATH) -> None:
    """Backfill solvability.oracle_plan_steps / verified and
    intervention_check.i_{plus,minus}_compilable for each bias, preserving the
    file's hand-authored comments and layout.

    Handles BOTH layouts: the round-3 block style
        solvability:
          verified: true
          oracle_plan_steps:
    and the legacy inline-brace style `solvability: { ... }`. Block style is
    detected per-bias and edited line-by-line; the brace style falls back to the
    original single-line substitution."""
    text = path.read_text(encoding="utf-8")
    by_id = {r["id"]: r for r in results}
    block_re = re.compile(r"(?ms)^- id: (?P<id>\S+)\b.*?(?=^- id: |\Z)")

    def edit(m: "re.Match") -> str:
        block = m.group(0)
        r = by_id.get(m.group("id"))
        if r is None:
            return block
        steps = "null" if r["oracle_plan_steps"] is None else str(r["oracle_plan_steps"])
        verified = str(r["verified"]).lower()
        iplus = str(r["i_plus_compilable"]).lower()
        iminus = str(r["i_minus_compilable"]).lower()
        if re.search(r"(?m)^    solvability:\s*\{", block):     # legacy inline form
            block = re.sub(r"(?m)^(    solvability: )\{[^}]*\}",
                           rf"\g<1>{{ verified: {verified}, "
                           rf"oracle_plan_steps: {steps} }}", block)
            block = re.sub(r"(?m)^(    intervention_check: )\{[^}]*\}",
                           rf"\g<1>{{ i_plus_compilable: {iplus}, "
                           rf"i_minus_compilable: {iminus} }}", block)
            return block
        block = _set_block_field(block, "oracle_plan_steps", steps, "    ")
        block = _set_block_field(block, "verified", verified, "    ")
        block = _set_block_field(block, "i_plus_compilable", iplus, "    ")
        block = _set_block_field(block, "i_minus_compilable", iminus, "    ")
        return block

    path.write_text(block_re.sub(edit, text), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="backfill biases.yaml solvability/intervention_check")
    args = ap.parse_args(argv)

    results = [check_bias(b) for b in load_biases(strict_actions=False)]
    report = {"step_cap": STEP_CAP,
              "circular_bias_rejected": circular_bias_rejected(),
              "biases": results}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for r in results:
        flag = "OK " if r["verified"] else "FAIL"
        print(f"{flag} {r['id']:3s} steps={r['oracle_plan_steps']} "
              f"I+={r['i_plus_compilable']} I-={r['i_minus_compilable']} "
              f"{r['reject_reason'] or ''}")
    print(f"circular-bias correctly rejected: {report['circular_bias_rejected']}")
    print(f"report -> {OUT_PATH}")
    bad = [r for r in results if not r["verified"]]
    if args.write and not bad:
        backfill_yaml(results)
        print(f"backfilled {BIASES_PATH}")
    return 1 if bad or not report["circular_bias_rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
