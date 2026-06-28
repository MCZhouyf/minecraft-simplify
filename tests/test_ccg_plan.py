"""Stage 6 offline tests: CCG graph planning, gate folding, pruning, persistence."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from Adam.tcpg.ccg import CCG                            # noqa: E402
from Adam.tcpg.proposer import Candidate                 # noqa: E402


def gate(action, target, prop, cmp_, val, dim="resource"):
    return Candidate(action, dim, target, prop, cmp_, val)


def test_plan_from_graph_closed_chain_zero_llm():
    g = CCG.init_default()
    plan = g.plan_from_graph("iron_pickaxe",
                             {"oak_log": 4, "crafting_table": 1,
                              "stone_pickaxe": 1, "coal": 2})
    assert plan is not None
    assert plan.index("mineIronOre") < plan.index("smeltRawIron") < plan.index("craftIronPickaxe")
    assert plan.count("mineIronOre") == 3 and plan.count("smeltRawIron") == 3


def test_plan_inventory_credit_and_missing_edge():
    g = CCG.init_default()
    assert g.plan_from_graph("furnace", {"furnace": 1}) == []        # already have
    assert g.plan_from_graph("ender_pearl", {}) is None              # outside graph


def test_inventory_gate_folds_into_demand_as_catalyst():
    g = CCG.init_default()
    g.write_back(gate("craftFurnace", "inventory_count", "coal", ">=", 1))
    plan = g.plan_from_graph("furnace", {"cobblestone": 8, "crafting_table": 1,
                                         "stone_pickaxe": 1})
    assert plan == ["gatherCoalOre", "craftFurnace"]
    # catalyst already satisfied -> no extra acquisition
    plan2 = g.plan_from_graph("furnace", {"cobblestone": 8, "crafting_table": 1,
                                          "coal": 1})
    assert plan2 == ["craftFurnace"]


def test_held_tool_gate_folds_to_tool_demand():
    g = CCG.init_default()
    g.write_back(gate("mineGoldOre", "held_tool", "tier", ">=", "diamond",
                      dim="capability"))
    plan = g.plan_from_graph("raw_gold", {"iron_pickaxe": 1, "stick": 2,
                                          "crafting_table": 1, "coal": 1})
    assert plan and "craftDiamondPickaxe" in plan
    assert plan.index("craftDiamondPickaxe") < plan.index("mineGoldOre")
    assert g.plan_from_graph("raw_gold", {"diamond_pickaxe": 1}) == ["mineGoldOre"]


def test_unfoldable_gate_falls_back_to_llm():
    g = CCG.init_default()
    g.write_back(gate("smeltRawIron", "time_of_day", "time", "in", [0, 12000],
                      dim="environment"))
    assert g.plan_from_graph("iron_ingot",
                             {"raw_iron": 1, "coal": 1}) is None     # LLM regime
    assert "time_of_day" in g.gate_text() and "[verified]" in g.gate_text()


def test_prune_known_false_gates_only():
    g = CCG.init_default()
    c = gate("mineGoldOre", "held_tool", "tier", ">=", "diamond", dim="capability")
    g.write_back(c)
    kept, pruned = g.prune(["gatherStone", "mineGoldOre", "craftPlanks"],
                           {c.cid: 0})
    assert kept == ["gatherStone", "craftPlanks"]
    assert pruned == [{"action": "mineGoldOre", "gate_cid": c.cid}]
    kept2, pruned2 = g.prune(["mineGoldOre"], {c.cid: None})         # unknown
    assert kept2 == ["mineGoldOre"] and not pruned2


def test_serialization_round_trip(tmp_path):
    g = CCG.init_default()
    g.write_back(gate("craftFurnace", "inventory_count", "coal", ">=", 1))
    g.reject(gate("craftFurnace", "y_level", "y", "<=", -16, dim="context"))
    f = tmp_path / "ccg.json"
    g.save(f)
    g2 = CCG.load(f)
    assert g2.to_dict() == g.to_dict()
    assert g2.plan_from_graph("furnace", {"cobblestone": 8, "crafting_table": 1,
                                          "coal": 1}) == ["craftFurnace"]
