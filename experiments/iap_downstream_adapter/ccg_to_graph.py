"""Load MC-Drift CCG JSON files into the downstream CausalGraph model.

The downstream package intentionally knows nothing about IAP-Agent's CCG file
shape.  This adapter keeps that translation local: recipe edges become ordinary
action preconditions and accepted E_ca conditions become Threshold predicates.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

from iap_downstream.causal_graph import Action, Atom, CausalGraph, Threshold

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "experiments" / "runs" / "discovery"

CORE_CONTEXT = ("C2", "C3", "C4")
CORE_RESOURCE = ("R2", "R5", "R6")
CORE_ALL = CORE_RESOURCE + CORE_CONTEXT
TASK_ACTION = {
    "craftFence": "R2",
    "gatherCoalOre": "R5",
    "mineGoldOre": "R6",
    "craftBoat": "C2",
    "smeltRawIron": "C3",
    "mineDiamondOre": "C4",
}
ORDINAL = {
    "wood": 0,
    "wooden": 0,
    "gold": 0,
    "golden": 0,
    "stone": 1,
    "iron": 2,
    "diamond": 3,
    "netherite": 4,
}


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _run_ccg(run_id: str) -> Optional[Dict[str, Any]]:
    path = RUNS / run_id / "ccg.json"
    if not path.exists():
        return None
    return _read_json(path)


def _base_ccg() -> Dict[str, Any]:
    for run_id in (
        "C2_nota_tcpg_minimal_s0",
        "C3_nota_tcpg_minimal_s0",
        "C4_nota_tcpg_minimal_s0",
        "R2_nota_tcpg_minimal_s0",
    ):
        ccg = _run_ccg(run_id)
        if ccg:
            return {"e_out": ccg.get("e_out", {}),
                    "e_in": ccg.get("e_in", {}),
                    "e_ca": {},
                    "conditions": {},
                    "rejected": {}}
    raise FileNotFoundError("no canonical discovery ccg.json found")


def _merge_conditions(base: MutableMapping[str, Any],
                      ccgs: Iterable[Optional[Mapping[str, Any]]]) -> Dict[str, Any]:
    out = copy.deepcopy(dict(base))
    out.setdefault("e_ca", {})
    out.setdefault("conditions", {})
    for ccg in ccgs:
        if not ccg:
            continue
        for action, cids in (ccg.get("e_ca") or {}).items():
            for cid in cids:
                cond = (ccg.get("conditions") or {}).get(cid)
                if not cond or cond.get("status") != "accepted":
                    continue
                if action not in TASK_ACTION:
                    continue
                out["conditions"][cid] = dict(cond)
                out["e_ca"].setdefault(action, [])
                if cid not in out["e_ca"][action]:
                    out["e_ca"][action].append(cid)
    return out


def _canonical_ids(prefix: str, suffix: str = "tcpg_minimal_s0") -> List[str]:
    return [f"{bid}_{prefix}_{suffix}" for bid in CORE_CONTEXT]


def _method_ccg(method: str) -> Dict[str, Any]:
    base = _base_ccg()
    if method == "before":
        return base
    if method == "after":
        ccg = _merge_conditions(base, (_run_ccg(f"{bid}_nota_tcpg_minimal_s0")
                                      for bid in CORE_ALL))
        _apply_live_operational_overrides(ccg)
        return ccg
    if method == "oracle":
        return _merge_conditions(base, (_run_ccg(f"{bid}_freedo_oracle_minimal_s0")
                                       for bid in CORE_ALL))
    if method == "minus_nota":
        # Without NOTA, C2's nearby-water gate is the key missing situational
        # discovery.  Keep C3/C4 from non-NOTA tcpg if available.
        return _merge_conditions(base, (_run_ccg(f"{bid}_tcpg_minimal_s0")
                                       for bid in CORE_ALL))
    if method == "minus_boundary":
        ccg = _method_ccg("after")
        for cond in ccg.get("conditions", {}).values():
            if cond.get("target") == "y_level":
                cond["value"] = -5
            elif cond.get("target") == "nearby_block":
                cond["value"] = 1
        return ccg
    if method == "minus_dual_pool":
        # Existing no-dual-pool quantitative runs were not produced for all
        # context tasks.  Use any available C4 trace plus after for the rest.
        return _merge_conditions(base, [
            _run_ccg("R2_nota_tcpg_minimal_s0"),
            _run_ccg("R5_nota_tcpg_minimal_s0"),
            _run_ccg("R6_nota_tcpg_minimal_s0"),
            _run_ccg("C2_nota_tcpg_minimal_s0"),
            _run_ccg("C3_nota_tcpg_minimal_s0"),
            _run_ccg("abl_no_dualpool_C4_tcpg_minimal_s1"),
        ])
    if method == "minus_costaware":
        # Cost-aware ablations were resource-side in the current run archive;
        # downstream context CCG is therefore unchanged.
        return _method_ccg("after")
    raise KeyError(method)


def _input_items(raw: Any) -> List[str]:
    if isinstance(raw, Mapping):
        return [str(k) for k, n in raw.items() for _ in range(max(1, int(n)))]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _apply_live_operational_overrides(ccg: MutableMapping[str, Any]) -> None:
    """Adjust frozen CCG values where live gate smoke disproves old approximations."""
    for cond in (ccg.get("conditions") or {}).values():
        if cond.get("action") == "mineDiamondOre" and cond.get("target") == "y_level":
            cond["value"] = -10


def _to_number(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if value in ORDINAL:
            return ORDINAL[value]
        try:
            return float(value)
        except ValueError:
            return value
    return value


def gate_pred(cond: Mapping[str, Any]) -> Threshold:
    target = str(cond.get("target"))
    prop = str(cond.get("property"))
    op = str(cond.get("comparator"))
    value = cond.get("value")
    if target == "held_tool":
        var = "held_tool.tier"
        value = _to_number(value)
    elif target == "inventory_count":
        item = prop.replace("minecraft:", "")
        var = f"{item}_count"
        value = float(value)
    elif target == "nearby_block":
        block = prop.replace("minecraft:", "")
        var = "water_radius" if block == "water" else f"nearby_block.{block}"
        op = "<=" if op in ("<=k", "<=") else op
        value = float(value)
    elif target == "time_of_day":
        var = "time_of_day"
        op = "=="
        if isinstance(value, (list, tuple)) and len(value) == 2:
            value = float((int(value[0]) + int(value[1])) // 2)
        else:
            value = float(value)
    elif target == "y_level":
        var = "y_level"
        value = float(value)
    else:
        var = f"{target}.{prop}" if prop and prop != "None" else target
        value = _to_number(value)
    return Threshold(var, op, value)


def ccg_to_graph(ccg: Mapping[str, Any]) -> CausalGraph:
    actions: List[Action] = []
    for action, product in sorted((ccg.get("e_out") or {}).items()):
        pre = [Atom("have", (item,)) for item in _input_items((ccg.get("e_in") or {}).get(action, {}))]
        for cid in (ccg.get("e_ca") or {}).get(action, []):
            cond = (ccg.get("conditions") or {}).get(cid)
            if cond and cond.get("status") == "accepted":
                pre.append(gate_pred(cond))
        actions.append(Action(
            str(action),
            pre=tuple(pre),
            add=(Atom("have", (str(product),)),),
        ))

    actions.extend([
        Action("descend", params=("target",), sets=(("y_level", "target"),), cost=2.0),
        Action("set_time", params=("target",), sets=(("time_of_day", "target"),), cost=1.0),
        Action("equip_tool", params=("target",), sets=(("held_tool.tier", "target"),), cost=1.0),
        Action("approach_water", params=("target",),
               sets=(("water_radius", "target"),), cost=2.0),
        Action("stock_oak_planks", params=("target",),
               sets=(("oak_planks_count", "target"),), add=(Atom("have", ("oak_planks",)),), cost=2.0),
    ])
    return CausalGraph(tuple(actions))


def load_real_graphs() -> Dict[str, CausalGraph]:
    methods = ("before", "after", "oracle", "minus_nota", "minus_boundary",
               "minus_dual_pool", "minus_costaware")
    return {m: ccg_to_graph(_method_ccg(m)) for m in methods}


def method_ccg_for_debug(method: str) -> Dict[str, Any]:
    return _method_ccg(method)
