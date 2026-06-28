"""Stage 6: conditional causal graph (paper Sec. 4.2).

G = (objects, actions, conditions; E_in, E_out, E_ca):
  * E_out / E_in : object produce/input edges with quantities — initialized
    from the domain interface (recipe tree + action vocabulary), correctable.
  * E_ca         : VERIFIED condition gating edges only; the ONLY writer is
    write_back(), and the only path to write_back is do-evidence (runtime.py).

Planner consumption:
  * plan_from_graph(goal, inventory) — classical backward chaining over the
    verified subgraph, ZERO LLM calls. inventory_count / held_tool gates fold
    into the item-demand recursion (their repair IS acquisition); any other
    unsatisfied gate type returns None -> the LLM planner takes over, fed by
    gate_text() (graph-insufficient regime).
  * prune(actions, gate_values) — drop plan steps violating verified gates
    that evaluate known-false (programmatic, no LLM).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Adam.tcpg.eventlog import log_event

TCPG_DIR = Path(__file__).resolve().parent
RECIPES: Dict[str, Any] = {
    k: v for k, v in json.loads((TCPG_DIR / "recipe_tree.json").read_text()).items()
    if isinstance(v, dict)}

# item -> producing ACTION (the domain interface; mirrors mc_drift ACTION_GOAL)
ITEM_ACTION = {
    "furnace": "craftFurnace", "iron_pickaxe": "craftIronPickaxe",
    "oak_fence": "craftFence", "oak_boat": "craftBoat", "oak_planks": "craftPlanks",
    "stick": "craftSticks", "crafting_table": "craftCraftingTable",
    "wooden_pickaxe": "craftWoodenPickaxe", "stone_pickaxe": "craftStonePickaxe",
    "diamond_pickaxe": "craftDiamondPickaxe", "wooden_shovel": "craftWoodenShovel",
    "raw_iron": "mineIronOre", "raw_gold": "mineGoldOre", "coal": "gatherCoalOre",
    "diamond": "mineDiamondOre", "sand": "gatherSand", "dirt": "gatherDirt",
    "cobblestone": "gatherStone", "stone": "gatherStone",
    "oak_log": "gatherWoodLog", "iron_ingot": "smeltRawIron",
    "gold_ingot": "smeltRawGold",
}
TIER_PICKAXE = {0: "wooden_pickaxe", 1: "stone_pickaxe",
                2: "iron_pickaxe", 3: "diamond_pickaxe"}
TIERS = ["wooden", "golden", "stone", "iron", "diamond", "netherite"]
PICKAXE_TIER_INDEX = {"wooden": 0, "stone": 1, "iron": 2,
                      "diamond": 3, "netherite": 4}


class CCG:
    def __init__(self) -> None:
        self.e_out: Dict[str, str] = {}           # action -> output item
        self.e_in: Dict[str, Dict[str, int]] = {}  # action -> {item: count}
        self.e_ca: Dict[str, List[str]] = {}      # action -> [cid, ...] (verified)
        self.conditions: Dict[str, Dict[str, Any]] = {}   # cid -> candidate dict
        self.rejected: Dict[str, Dict[str, Any]] = {}

    # ----------------------------------------------------------- construction
    @classmethod
    def init_default(cls) -> "CCG":
        """Domain-interface initialization (E_in/E_out from the recipe tree)."""
        g = cls()
        for item, action in ITEM_ACTION.items():
            node = RECIPES.get(item)
            if node is None:
                continue
            g.e_out[action] = item
            if node["via"] == "craft":
                g.e_in[action] = dict(node["inputs"])
            elif node["via"] == "smelt":
                g.e_in[action] = {node["input"]: 1, node["fuel"]: 1}
            else:
                g.e_in[action] = {}
        return g

    # ----------------------------------------------------------- write paths
    def is_known_input_edge(self, cand) -> bool:
        """True only if `cand` restates a recipe input AT OR BELOW its vanilla
        quantity (e.g. 'smeltRawIron needs raw_iron>=1' with vanilla = 1) — a
        valid necessity confirmation, NOT a discovery. An ELEVATED requirement on
        a recipe input (n > the vanilla quantity, e.g. craftFence oak_planks>=9
        when vanilla fence needs 4) is a QUANTITY DRIFT and must be written back
        as a discovered gate, not discarded as a known edge. This prevents the
        confound-mirror failure (vanilla inputs re-accepted as new) WITHOUT also
        swallowing genuine count-threshold drifts on those same inputs."""
        d = cand.to_dict() if hasattr(cand, "to_dict") else dict(cand)
        if d.get("target") != "inventory_count":
            return False
        known = {str(k): v for k, v in self.e_in.get(d["action"], {}).items()}
        prop = str(d.get("property"))
        if prop not in known:
            return False
        try:
            n = float(d.get("value"))
        except (TypeError, ValueError):
            return True                     # non-numeric on a recipe input: plain edge
        return n <= float(known[prop])      # elevated threshold (drift) -> NOT known

    def write_back(self, cand, trial_id: str = "-", step: int = -1) -> None:
        d = cand.to_dict() if hasattr(cand, "to_dict") else dict(cand)
        if self.is_known_input_edge(cand):
            # Confirms a known E_in edge — record as confirmation, do NOT add a
            # new E_ca gate and do NOT mark as a discovered 'accepted' gate.
            d["status"] = "confirmed_known"
            self.conditions[d["cid"]] = d
            log_event("writeback", {"cid": d["cid"], "decision": "confirmed_known",
                                    "action": d["action"], "target": d["target"],
                                    "note": "known E_in edge, not a new gate"},
                      trial_id, step)
            return
        d["status"] = "accepted"
        self.conditions[d["cid"]] = d
        self.e_ca.setdefault(d["action"], [])
        if d["cid"] not in self.e_ca[d["action"]]:
            self.e_ca[d["action"]].append(d["cid"])
        log_event("writeback", {"cid": d["cid"], "decision": "accepted",
                                "action": d["action"], "target": d["target"],
                                "value": d["value"]}, trial_id, step)

    def reject(self, cand, trial_id: str = "-", step: int = -1) -> None:
        d = cand.to_dict() if hasattr(cand, "to_dict") else dict(cand)
        d["status"] = "rejected"
        self.rejected[d["cid"]] = d
        log_event("writeback", {"cid": d["cid"], "decision": "rejected"},
                  trial_id, step)

    def decided_cids(self) -> set:
        return set(self.conditions) | set(self.rejected)

    # ----------------------------------------------------------- planner feeds
    def assumed_preconds(self, action: str) -> List[Dict[str, Any]]:
        """Object-input preconditions of `action` as predicate dicts — the
        necessity-test candidate source on success (Sec. 4.1)."""
        preds = [{"dimension": "resource", "target": "inventory_count",
                  "property": item, "comparator": ">=", "value": n}
                 for item, n in self.e_in.get(action, {}).items()]
        out_item = self.e_out.get(action)
        node = RECIPES.get(out_item) if out_item else None
        if node and node.get("via") == "mine" and node.get("tool_tier") is not None:
            # tool_tier index (0..3) -> tier name in Sigma_MC's pickaxe ladder
            preds.append({"dimension": "capability", "target": "held_tool",
                          "property": "tier", "comparator": ">=",
                          "value": ["wooden", "stone", "iron", "diamond"][node["tool_tier"]]})
        return preds

    def gate_text(self) -> str:
        """Verified-gate lines for planner/actor prompt injection."""
        lines = []
        for action, cids in sorted(self.e_ca.items()):
            for cid in cids:
                c = self.conditions[cid]
                lines.append(f"[verified] {action} requires "
                             f"{c['target']}({c['property']}) {c['comparator']} "
                             f"{c['value']}")
        return ("\nVerified environment gates (hard constraints):\n"
                + "\n".join(lines) + "\n") if lines else ""

    # ----------------------------------------------------------- graph planning
    def plan_from_graph(self, goal_item: str, inventory: Dict[str, int]
                        ) -> Optional[List[str]]:
        """Backward chaining over the verified subgraph -> ordered action list,
        or None when the goal/gates fall outside the graph (LLM regime)."""
        inv = dict(inventory or {})
        plan: List[str] = []
        visiting: set = set()

        def best_pickaxe_tier() -> int:
            for t in (3, 2, 1, 0):
                if inv.get(TIER_PICKAXE[t], 0) >= 1:
                    return t
            return -1

        def ensure(item: str, need: int) -> bool:
            have = inv.get(item, 0)
            take = min(have, need)
            inv[item] = have - take
            need -= take
            if need <= 0:
                return True
            if item in visiting:
                return False
            action = ITEM_ACTION.get(item)
            node = RECIPES.get(item)
            if action is None or node is None:
                return False
            visiting.add(item)
            try:
                out_count = node.get("out_count", 1)
                batches = math.ceil(need / out_count)
                if node["via"] == "craft":
                    for ing, n_per in node["inputs"].items():
                        if not ensure(ing, n_per * batches):
                            return False
                elif node["via"] == "smelt":
                    if not ensure(node["input"], need):
                        return False
                    if not ensure(node["fuel"], max(1, math.ceil(need / 8))):
                        return False
                elif node["via"] == "mine":
                    tier = node.get("tool_tier")
                    if tier is not None and best_pickaxe_tier() < tier:
                        if not ensure(TIER_PICKAXE[tier], 1):
                            return False
                # verified gates on the producing action
                for cid in self.e_ca.get(action, []):
                    c = self.conditions[cid]
                    if c["target"] == "inventory_count":
                        if not ensure(str(c["property"]), int(c["value"])):
                            return False
                        inv[str(c["property"])] = inv.get(str(c["property"]), 0) \
                            + int(c["value"])          # catalyst: kept, not consumed
                    elif c["target"] == "held_tool":
                        want = PICKAXE_TIER_INDEX[str(c["value"])]
                        if best_pickaxe_tier() < want:
                            if not ensure(TIER_PICKAXE[min(want, 3)], 1):
                                return False
                    else:
                        return False                    # gate outside graph algebra
                plan.extend([action] * batches)
                inv[item] = inv.get(item, 0) + batches * out_count - need
                return True
            finally:
                visiting.discard(item)

        ok = ensure(goal_item, 1)
        return plan if ok else None

    def prune(self, actions: List[str], gate_values: Dict[str, int],
              trial_id: str = "-", step: int = -1
              ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """Drop steps whose verified gate evaluates known-FALSE (value 0 in
        gate_values, keyed by cid); unknown gates are NOT pruned."""
        kept, pruned = [], []
        for a in actions:
            bad = next((cid for cid in self.e_ca.get(a, [])
                        if gate_values.get(cid) == 0), None)
            if bad:
                pruned.append({"action": a, "gate_cid": bad})
                log_event("prune", {"plan_step_pruned": a, "gate_cid": bad},
                          trial_id, step)
            else:
                kept.append(a)
        return kept, pruned

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> Dict[str, Any]:
        return {"e_out": self.e_out, "e_in": self.e_in, "e_ca": self.e_ca,
                "conditions": self.conditions, "rejected": self.rejected}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CCG":
        g = cls()
        g.e_out = dict(d.get("e_out", {}))
        g.e_in = {k: dict(v) for k, v in d.get("e_in", {}).items()}
        g.e_ca = {k: list(v) for k, v in d.get("e_ca", {}).items()}
        g.conditions = dict(d.get("conditions", {}))
        g.rejected = dict(d.get("rejected", {}))
        return g

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CCG":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
