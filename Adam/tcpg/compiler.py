"""Stage 5: intervention compiler (contract K5) — the operational core of
"interventions as plans": do(h=1)/do(h=0) compile to primitive-action plans
in the agent's own action space, with paired undo segments.

compile(candidate, state) -> K5 dict:
  {candidate_id, feasible, est_steps, plan_plus, plan_minus,
   undo_plus, undo_minus, irreversible, infeasible_reason}
Plans are PURE DATA (primitive-call lists); generation and execution are
strictly separated (executor.py renders them to JS).

Primitive call shapes (executor contract):
  {"primitive":"mineBlock","args":{"name":..,"count":..}}            # also special:"roof_column"
  {"primitive":"craftItem","args":{"name":..,"count":..}}
  {"primitive":"smeltItem","args":{"name":..,"fuel":..,"count":..}}
  {"primitive":"placeItem","args":{"name":..,"where":"near"|"on_last"|"roof"}}
  {"primitive":"useChest","args":{"op":"deposit"|"withdraw","items":[{"name":..,"count":..}]}}
  {"primitive":"equip","args":{"name":..}}        # name=None -> stash held to chest
  {"primitive":"moveTo","args":{"y":..}|{"dx":..}}
  {"primitive":"moveToBlock","args":{"name":..,"radius":..,"maxDistance":..}}
  {"primitive":"wait","args":{"until_in":[a,b]}|{"until_out":[a,b]}}

Stash discipline: do(h=0) for resources uses useChest deposit (never destroys
items); undo withdraws. INFEASIBLE reasons: no_macro | recipe_unreachable |
exceeds_step_cap | not_intervenable.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Adam.tcpg import predicates as P
from Adam.tcpg.ccg import ITEM_ACTION

log = logging.getLogger("Adam.tcpg.compiler")

TCPG_DIR = Path(__file__).resolve().parent
RECIPES: Dict[str, Any] = json.loads((TCPG_DIR / "recipe_tree.json").read_text())
COSTS: Dict[str, Any] = json.loads((TCPG_DIR / "cost_table.json").read_text())
# action -> its output item (reverse of ccg.ITEM_ACTION); used to find a craft
# action's recipe co-inputs for boundary-test isolation. Mining/gathering actions
# may collide harmlessly (no recipe inputs to top up); craft actions are 1:1.
ACTION_OUTPUT = {action: item for item, action in ITEM_ACTION.items()}

# Resource/capability gating variables (paper 4.4): their I+/I- contrast value is
# INJECTED via env.reset of player state (inventory/equipment) at low, flat cost
# -- ADAM's controlled-initial-configuration isolation. Declared per-target in the
# signature as realize=="reset" (see schema.json); currently inventory_count /
# held_tool / held_item. Situational-constraint targets (y_level / time_of_day /
# nearby_block / sky_exposed / ...) are realize=="in_world": their condition must
# be reached by REAL exploration and keeps the est_steps (scaled) cost.
SIM_VERIFIABLE_TARGETS = frozenset(
    t for t, spec in P.schema()["primitives"].items()
    if spec.get("realize") == "reset")

TIERS = ["wooden", "golden", "stone", "iron", "diamond", "netherite"]
TIER_PICKAXE = {0: "wooden_pickaxe", 1: "stone_pickaxe",
                2: "iron_pickaxe", 3: "diamond_pickaxe"}
PICKAXE_TIER_INDEX = {"wooden": 0, "stone": 1, "iron": 2,
                      "diamond": 3, "netherite": 4}
PLACEABLE_STATIONS = {"furnace", "crafting_table", "chest"}
NAVIGABLE_NATURAL_BLOCKS = {"water", "lava", "flowing_water", "flowing_lava"}
_STATION_RADIUS = 3            # default proximity for station_type interventions
TOOL_CATEGORY_DEFAULT = {"shovel": "wooden_shovel", "pickaxe": "wooden_pickaxe",
                         "axe": "wooden_axe", "sword": "wooden_sword",
                         "hoe": "wooden_hoe"}
STEP_CAP_DEFAULT = 500.0

DEFAULT_STATE = {"inventory": {}, "agent_y": 70.0, "held": None, "time": 6000}


class Infeasible(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _u(name: str) -> float:
    return float(COSTS["unit"][name])


def _mine_est(block: str, probabilistic: bool = False) -> float:
    base = float(COSTS["mine_est"].get(block, 20))
    return base * (COSTS["probabilistic_factor"] if probabilistic else 1.0)


def call(primitive: str, **args) -> Dict[str, Any]:
    return {"primitive": primitive, "args": args}


# =================================================================== acquisition
def acquire_plan(item: str, count: int, inventory: Dict[str, int],
                 recipes: Optional[Dict[str, Any]] = None,
                 _visited: Optional[frozenset] = None
                 ) -> Tuple[List[Dict], float]:
    """Recursive resource-acquisition subplanning over the recipe tree, with
    inventory credit and a cycle guard. Returns (steps, est). Raises Infeasible.
    NOTE: mutates a local copy of `inventory` credit, not the caller's dict."""
    recipes = recipes if recipes is not None else RECIPES
    inv = dict(inventory)
    visited = _visited or frozenset()

    def go(name: str, need: int, seen: frozenset) -> Tuple[List[Dict], float]:
        have = inv.get(name, 0)
        take = min(have, need)
        if take:
            inv[name] = have - take
            need -= take
        if need <= 0:
            return [], 0.0
        if name in seen:
            raise Infeasible("recipe_unreachable")     # cycle (e.g. circular bias)
        node = recipes.get(name)
        if node is None:
            raise Infeasible("recipe_unreachable")
        seen = seen | {name}
        steps: List[Dict] = []
        est = 0.0

        if node["via"] == "mine":
            tier = node.get("tool_tier")
            if tier is not None:
                tool = TIER_PICKAXE[tier]
                if inv.get(tool, 0) < 1 and not any(
                        inv.get(TIER_PICKAXE[t], 0) for t in range(tier, 4)):
                    s2, e2 = go(tool, 1, seen)
                    steps += s2
                    est += e2
                    inv[tool] = inv.get(tool, 0) + 1
                steps.append(call("equip", name=_best_pickaxe(inv, tier)))
                est += _u("equip")
            steps.append(call("mineBlock", name=node["block"], count=need))
            est += need * _mine_est(node["block"], node.get("probabilistic", False))
            inv[name] = inv.get(name, 0)               # consumed by caller
            return steps, est

        if node["via"] == "craft":
            batches = math.ceil(need / node.get("out_count", 1))
            for ing, n_per in node["inputs"].items():
                s2, e2 = go(ing, n_per * batches, seen)
                steps += s2
                est += e2
            if node.get("station") == "crafting_table" and inv.get("crafting_table", 0) < 1:
                s2, e2 = go("crafting_table", 1, seen)
                steps += s2
                est += e2
                inv["crafting_table"] = 1
                steps.append(call("placeItem", name="crafting_table", where="near"))
                est += _u("place")
            steps.append(call("craftItem", name=name, count=batches))
            est += batches * _u("craft")
            leftover = batches * node.get("out_count", 1) - need
            if leftover > 0:
                inv[name] = inv.get(name, 0) + leftover
            return steps, est

        if node["via"] == "smelt":
            s2, e2 = go(node["input"], need, seen)
            steps += s2
            est += e2
            s3, e3 = go(node["fuel"], max(1, math.ceil(need / 8)), seen)
            steps += s3
            est += e3
            if inv.get("furnace", 0) < 1:
                s4, e4 = go("furnace", 1, seen)
                steps += s4
                est += e4
                inv["furnace"] = 1
            steps.append(call("placeItem", name="furnace", where="near"))
            steps.append(call("smeltItem", name=name, fuel=node["fuel"], count=need))
            est += _u("place") + need * _u("smelt")
            return steps, est

        raise Infeasible("recipe_unreachable")

    return go(item, count, visited)


def _best_pickaxe(inv: Dict[str, int], min_tier: int) -> str:
    for t in range(min_tier, 4):
        if inv.get(TIER_PICKAXE[t], 0) >= 1:
            return TIER_PICKAXE[t]
    return TIER_PICKAXE[min_tier]


def _ensure_chest(inv: Dict[str, int]) -> Tuple[List[Dict], float]:
    """Chest available for stashing: acquire+place if absent (runtime reuses a
    nearby chest when one exists; executor places only if none found)."""
    steps: List[Dict] = []
    est = 0.0
    if inv.get("chest", 0) < 1:
        s, e = acquire_plan("chest", 1, inv)
        steps += s
        est += e
    steps.append(call("placeItem", name="chest", where="near"))
    return steps, est + _u("place")


# =================================================================== templates
def _t_inventory_count(c, st):
    item, need_n = c.property, int(c.value)
    have = st["inventory"].get(item, 0)
    plus: List[Dict] = []
    est_p = 0.0
    if have < need_n:
        plus, est_p = acquire_plan(item, need_n - have, st["inventory"])
    chest_s, chest_e = _ensure_chest(st["inventory"])
    minus = chest_s + [call("useChest", op="deposit",
                            items=[{"name": item, "count": max(have, need_n)}])]
    undo_plus = chest_s + [call("useChest", op="deposit",
                                items=[{"name": item, "count": max(need_n - have, 0)}])]
    undo_minus = [call("useChest", op="withdraw",
                       items=[{"name": item, "count": max(have, need_n)}])]
    return plus, minus, undo_plus, undo_minus, est_p, chest_e + _u("chest_op"), False


def _t_held_tool(c, st):
    try:
        want_tier = PICKAXE_TIER_INDEX[str(c.value)]
    except KeyError as exc:
        raise Infeasible("no_macro") from exc
    tool = TIER_PICKAXE.get(min(want_tier, 3))
    if tool is None:
        raise Infeasible("no_macro")
    # held_tool is a player-state intervention (schema realize=reset), so K5
    # must not realize it by mining/crafting a tool in-world. Exact-set the
    # requested tool, then equip it; this keeps the verification focused on
    # the held-tool predicate instead of the resource plan used to obtain it.
    plus: List[Dict] = [call("set_count", name=tool, count=1, special="exact"),
                        call("equip", name=tool)]
    lower = next((TIER_PICKAXE[t] for t in range(min(want_tier, 3) - 1, -1, -1)
                  if st["inventory"].get(TIER_PICKAXE[t], 0) >= 1), None)
    if lower:
        minus = [call("set_count", name=lower, count=1, special="exact"),
                 call("equip", name=lower)]
        undo_minus = [call("equip", name=st.get("held") or tool)]
        est_m = _u("equip") + _u("set_count")
    else:                                   # bare hand via stash
        chest_s, chest_e = _ensure_chest(st["inventory"])
        held = st.get("held") or tool
        minus = chest_s + [call("useChest", op="deposit",
                                items=[{"name": held, "count": 1}])]
        undo_minus = [call("useChest", op="withdraw",
                           items=[{"name": held, "count": 1}]),
                      call("equip", name=held)]
        est_m = chest_e + _u("chest_op")
    undo_plus: List[Dict] = [call("set_count", name=tool, count=0, special="exact")]
    if st.get("held"):
        undo_plus.append(call("equip", name=st["held"]))
    return plus, minus, undo_plus, undo_minus, _u("equip") + _u("set_count"), est_m, False


def _t_held_item(c, st):
    item = TOOL_CATEGORY_DEFAULT.get(str(c.value), str(c.value))
    plus: List[Dict] = []
    est_p = 0.0
    if st["inventory"].get(item, 0) < 1 and st.get("held") != item:
        plus, est_p = acquire_plan(item, 1, st["inventory"])
    plus = plus + [call("equip", name=item)]
    chest_s, chest_e = _ensure_chest(st["inventory"])
    held = st.get("held") or item
    minus = chest_s + [call("useChest", op="deposit", items=[{"name": held, "count": 1}])]
    undo_plus = ([call("equip", name=st["held"])] if st.get("held")
                 else [call("useChest", op="deposit", items=[{"name": item, "count": 1}])])
    undo_minus = [call("useChest", op="withdraw", items=[{"name": held, "count": 1}]),
                  call("equip", name=held)]
    return plus, minus, undo_plus, undo_minus, est_p + _u("equip"), chest_e + _u("chest_op"), False


def _t_nearby_block(c, st):
    # nearby_block candidates carry value = search RADIUS (int). A malformed
    # value (e.g. a block name that should have been a station_type candidate)
    # is not intervenable via this template -> no_macro, never a crash (K5 must
    # not raise on any well-typed candidate).
    block = c.property
    try:
        k = int(c.value)
    except (TypeError, ValueError):
        raise Infeasible("no_macro")
    if block in NAVIGABLE_NATURAL_BLOCKS:
        radius = max(1, k)
        # Natural fluids are external context: satisfy nearby_block by moving to
        # an authored/world water body, then undo by moving outside the radius.
        plus = [call("moveToBlock", name=block, radius=radius, maxDistance=32)]
        minus = [call("moveTo", dx=radius + 3)]
        undo_plus = [call("moveTo", dx=-(radius + 3))]
        undo_minus = [call("moveToBlock", name=block, radius=radius, maxDistance=32)]
        move_est = (radius + 3) * _u("move_per_block")
        return plus, minus, undo_plus, undo_minus, move_est, move_est, False
    if block not in PLACEABLE_STATIONS:
        raise Infeasible("no_macro")        # cannot statically compile "go find an ore"
    plus: List[Dict] = []
    est_p = 0.0
    if st["inventory"].get(block, 0) < 1:
        plus, est_p = acquire_plan(block, 1, st["inventory"])
    plus = plus + [call("placeItem", name=block, where="near")]
    minus = [call("moveTo", dx=k + 3)]
    undo_plus = [call("mineBlock", name=block, count=1)]
    undo_minus = [call("moveTo", dx=-(k + 3))]
    move_est = (k + 3) * _u("move_per_block")
    return plus, minus, undo_plus, undo_minus, est_p + _u("place"), move_est, False


def _t_station_type(c, st):
    # station_type candidates carry value = STATION BLOCK NAME (not a radius):
    #   target=station_type, property=type, comparator="=", value=crafting_table
    # I+ : make a station of that type present nearby (acquire + place, fixed
    #      radius _STATION_RADIUS); I- : move away from it. Only placeable
    #      stations are intervenable.
    block = str(c.value)
    if block not in PLACEABLE_STATIONS:
        raise Infeasible("no_macro")
    k = _STATION_RADIUS
    plus: List[Dict] = []
    est_p = 0.0
    if st["inventory"].get(block, 0) < 1:
        plus, est_p = acquire_plan(block, 1, st["inventory"])
    plus = plus + [call("placeItem", name=block, where="near")]
    minus = [call("moveTo", dx=k + 3)]
    undo_plus = [call("mineBlock", name=block, count=1)]
    undo_minus = [call("moveTo", dx=-(k + 3))]
    move_est = (k + 3) * _u("move_per_block")
    return plus, minus, undo_plus, undo_minus, est_p + _u("place"), move_est, False


def _t_y_level(c, st):
    v = float(c.value)
    cur = float(st.get("agent_y", DEFAULT_STATE["agent_y"]))
    lo, hi = (v - 2, v + 6) if c.comparator == "<=" else (v + 2, v - 6)
    plus = [call("set_y", y=lo)]
    minus = [call("set_y", y=hi)]
    back = [call("set_y", y=round(cur))]
    est = lambda _t: _u("set_state")
    return plus, minus, back, list(back), est(lo), est(hi), False


def _t_sky_exposed(c, st):
    open_roof = [call("mineBlock", name="_roof", special="roof_column", count=6)]
    close_roof = [call("placeItem", name="dirt", where="roof")]
    want = (str(c.value) == "true" or c.value is True)
    plus, minus = (open_roof, close_roof) if want else (close_roof, open_roof)
    return plus, minus, list(minus), list(plus), 6.0, _u("place"), False


def _t_time_of_day(c, st):
    a, b = [int(x) for x in c.value]
    # A full-day window makes the I- side (wait until OUTSIDE [a,b]) unsatisfiable
    # -> the wait can never complete (caused real HTTP timeouts). Such a gate is
    # not intervenable; degrade to no_macro rather than emit an impossible wait.
    DAY = 24000
    if (b - a) >= DAY or (a <= 0 and b >= DAY):
        raise Infeasible("no_macro")
    plus_tick = int((a + b) // 2) % DAY
    minus_tick = int((b + 2000) % DAY)
    # Command-set the environmental state, then retry the original action.
    # This is still an in-world intervention: it changes only time, not outcome.
    plus = [call("set_time", tick=plus_tick)]
    minus = [call("set_time", tick=minus_tick)]
    cur = int(st.get("time_of_day", DEFAULT_STATE.get("time_of_day", 6000))) % DAY
    undo = [call("set_time", tick=cur)]
    return plus, minus, undo, list(undo), _u("set_state"), _u("set_state"), False


def _t_station_base(c, st):
    if str(c.value) != "stone":
        raise Infeasible("no_macro")
    inv = st["inventory"]
    plus: List[Dict] = []
    est_p = 0.0
    for need_item in ("cobblestone", "furnace"):
        if inv.get(need_item, 0) < 1:
            s, e = acquire_plan(need_item, 1, inv)
            plus += s
            est_p += e
    plus += [call("placeItem", name="cobblestone", where="near"),
             call("placeItem", name="furnace", where="on_last")]
    minus_pre: List[Dict] = []
    est_m = 0.0
    if inv.get("furnace", 0) < 1:
        minus_pre, est_m = acquire_plan("furnace", 1, inv)
    minus = minus_pre + [call("placeItem", name="dirt", where="near"),
                         call("placeItem", name="furnace", where="on_last")]
    undo = [call("mineBlock", name="furnace", count=1)]
    return plus, minus, list(undo), list(undo), est_p + 2 * _u("place"), est_m + 2 * _u("place"), False


TEMPLATES = {
    "inventory_count": _t_inventory_count,
    "held_tool": _t_held_tool,
    "held_item": _t_held_item,
    "nearby_block": _t_nearby_block,
    "station_type": _t_station_type,        # value is a block name, not a radius
    "y_level": _t_y_level,
    "sky_exposed": _t_sky_exposed,
    "time_of_day": _t_time_of_day,
    "station_base_block": _t_station_base,
}


# =================================================================== admitted
def _translate_admitted(c) -> Optional[Any]:
    """Single-comparison admitted expressions translate to a core-equivalent
    candidate; anything richer stays observe-only (no_macro)."""
    from Adam.tcpg.admission import FIELD_FAMILY, load_runtime, parse
    entry = next((e for e in load_runtime()["admitted"]
                  if e["property_name"] == c.target), None)
    if entry is None or not entry.get("intervenable"):
        return None
    ast = parse(entry["expr"])
    if ast[0] == "cmp" and ast[2][0] == "field" and ast[3][0] == "num":
        field, op, num = ast[2], ast[1], ast[3][1]
        fam = FIELD_FAMILY.get(field[1])
        from Adam.tcpg.proposer import Candidate
        if fam == "y_level":
            return Candidate(c.action, c.dimension, "y_level", "y", op, num)
        if fam == "inventory_count" and field[2]:
            return Candidate(c.action, c.dimension, "inventory_count",
                             field[2], op, int(num))
    if ast[0] == "eq" and ast[1][0] == "field":
        fam = FIELD_FAMILY.get(ast[1][1])
        from Adam.tcpg.proposer import Candidate
        if fam == "sky_exposed":
            return Candidate(c.action, c.dimension, "sky_exposed", "sky", "=", ast[2])
    return None


# =================================================================== entry points
def _exact_settable_numeric(target: str) -> Optional[str]:
    """Monotone direction ('up'/'down') if `target` is an exact-settable
    monotone numeric per the signature, else None. Signature-driven (no
    hardcoded target list beyond the realization map in _exact_set_calls)."""
    prim = P.schema()["primitives"].get(target) or {}
    mono = prim.get("monotone")
    return mono if (prim.get("exact_settable") and mono in ("up", "down")) else None


def _exact_set_calls(target: str, prop: str, value: int) -> List[Dict]:
    """Plan that sets a monotone numeric property EXACTLY to `value`.
    inventory_count -> set_count (clear+give in-world; a sim-verifiable reset);
    y_level -> moveTo an absolute y."""
    if target == "inventory_count":
        return [call("set_count", name=prop, count=int(value), special="exact")]
    if target == "y_level":
        return [call("set_y", y=float(value))]
    raise Infeasible("no_exact_set")


def _co_input_topups(c) -> List[Dict]:
    """Isolation for inventory_count boundary tests: when the gated item is one
    input of a CRAFT recipe, set the action's OTHER recipe inputs to a sufficient,
    per-attempt-fresh level so the action can't fail for an unrelated input.
    Removes the co-input-depletion confound (e.g. sticks running out across the
    fence I+/I- attempts -> non-monotone evidence). Gated by the signature
    isolated==False; no-op for isolated targets or non-craft actions."""
    if c.target != "inventory_count":
        return []
    if P.schema()["primitives"].get("inventory_count", {}).get("isolated", True):
        return []
    node = RECIPES.get(ACTION_OUTPUT.get(c.action))
    if not node or node.get("via") != "craft":
        return []
    return [call("set_count", name=ing, count=int(n_per), special="exact")
            for ing, n_per in node.get("inputs", {}).items()
            if str(ing) != str(c.property)]     # the tested var is set by the boundary


def _boundary_template(c, st):
    """Boundary intervention for an exact-settable monotone numeric threshold.

    I+ sets x EXACTLY to n (the boundary value that SATISFIES x CMP n); I- sets
    x to the adjacent value that VIOLATES it (n-1 for '>=', n+1 for '<='). Under
    a monotone gate x CMP theta*, only n == theta* yields a pos-success /
    neg-fail contrast, so the threshold is uniquely identifiable -- no overshoot
    ambiguity (acquiring 'to >= n' could overshoot past theta* and falsely pass).
    Undo restores x to the observed value. Cost: inventory_count is
    sim-verifiable so the runtime overrides est with the flat sim_verify_cost;
    y_level keeps a distance-proportional est."""
    target, prop = c.target, c.property
    n = int(round(float(c.value)))
    if target == "inventory_count":
        cur = int(st["inventory"].get(prop, 0))
    elif target == "y_level":
        cur = int(round(float(st.get("agent_y", DEFAULT_STATE["agent_y"]))))
    else:
        raise Infeasible("no_exact_set")
    if c.comparator == ">=":
        n_pos, n_neg = n, n - 1
    elif c.comparator == "<=":
        n_pos, n_neg = n, n + 1
    else:
        raise Infeasible("boundary_needs_inequality")
    co = _co_input_topups(c)                     # hold co-inputs sufficient & fixed
    plus = co + _exact_set_calls(target, prop, n_pos)
    minus = co + _exact_set_calls(target, prop, n_neg)
    undo = _exact_set_calls(target, prop, cur)
    if target == "y_level":
        est_p = est_m = _u("set_state")
    else:
        est_p = est_m = _u("chest_op")
    return plus, minus, list(undo), list(undo), est_p, est_m, False


def compile(candidate, state: Optional[Dict[str, Any]] = None,
            step_cap: float = STEP_CAP_DEFAULT) -> Dict[str, Any]:
    st = {**DEFAULT_STATE, **(state or {})}
    st["inventory"] = dict(st.get("inventory") or {})
    out = {"candidate_id": candidate.cid, "feasible": False, "est_steps": 0.0,
           "plan_plus": [], "plan_minus": [], "undo_plus": [], "undo_minus": [],
           "irreversible": False, "infeasible_reason": None,
           "sim_verifiable": candidate.target in SIM_VERIFIABLE_TARGETS}
    try:
        target = candidate.target
        c = candidate
        if getattr(candidate, "origin", "core") == "admitted" or \
                target not in TEMPLATES and target not in ("weather", "ingredient_type"):
            translated = _translate_admitted(candidate)
            if translated is None and target not in TEMPLATES:
                raise Infeasible("no_macro")
            c = translated or candidate
            target = c.target
        if target == "weather":
            raise Infeasible("not_intervenable")
        if target == "ingredient_type":
            raise Infeasible("no_macro")
        if getattr(c, "source", "") == "frontier" and \
                _exact_settable_numeric(target) is not None:
            plus, minus, undo_p, undo_m, est_p, est_m, irrev = _boundary_template(c, st)
        else:
            plus, minus, undo_p, undo_m, est_p, est_m, irrev = TEMPLATES[target](c, st)
        est = est_p + est_m
        if est > step_cap:
            raise Infeasible("exceeds_step_cap")
        out.update(feasible=True, est_steps=round(est, 1),
                   plan_plus=plus, plan_minus=minus,
                   undo_plus=undo_p, undo_minus=undo_m, irreversible=irrev)
    except Infeasible as exc:
        out["infeasible_reason"] = exc.reason
    except Exception as exc:  # noqa: BLE001
        # K5 robustness contract: a shape-mismatched or otherwise malformed
        # candidate must degrade to no_macro, never propagate into the runtime
        # loop (which would abort the whole verification episode).
        out["infeasible_reason"] = "no_macro"
        out["compile_error"] = f"{type(exc).__name__}: {exc}"
        log.warning("compile() degraded candidate %s to no_macro: %s",
                    getattr(candidate, "cid", "?"), exc)
    return out


def dry_run(candidate, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    k5 = compile(candidate, state)
    return {"feasible": k5["feasible"], "est_steps": k5["est_steps"],
            "infeasible_reason": k5["infeasible_reason"],
            "i_plus_compilable": k5["feasible"],
            "i_minus_compilable": k5["feasible"]}


def match_macro_family(used_fields) -> Optional[str]:
    from Adam.tcpg.admission import match_macro_family as _m
    return _m(set(used_fields))
