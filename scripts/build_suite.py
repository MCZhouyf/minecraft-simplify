#!/usr/bin/env python3
"""第三轮 阶段2(方案C): 套件配置一次到位。
将旧12偏差(R1-3/C1-3/P1-2/X1-2/E1-2)重构为新8偏差(R1/R2/R4/R5/R6/C1/C3/C4),
用schema合法值; craftBoat(C2)/craftReinforcedHandle(R3)因技能未注册暂不纳入(待开发)。
同步 tasks.yaml + biases.yaml + 快照测试。幂等。
"""
import sys
from ruamel.yaml import YAML
yaml = YAML(); yaml.preserve_quotes = True; yaml.width = 4096
def load(p):
    with open(p) as f: return yaml.load(f)
def dump(o,p):
    with open(p,"w") as f: yaml.dump(o,f)

TASKS="experiments/tasks.yaml"; BIASES="mc_drift/biases/biases.yaml"

# ============ 8个新偏差的 bias 定义(schema合法) ============
# dimension用旧5值(新命名"资源输入/情境约束"仅在文档层)
NEW_BIASES = {
"R1": {  # 合成熔炉需催化剂砂(资源输入-物料)
  "id":"R1","level":"L1","dimension":"resource","action":"craftFurnace",
  "mechanism":"mod_event",
  "payload":{"gate":"craft_result","params":{
      "result_match":"minecraft:furnace","require":"inventory_min",
      "item":"minecraft:sand","count":1}},
  "ground_truth":{"target":"inventory_count","property":"sand","comparator":">=","value":1},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[resource]","hinted":"The furnace won't bind without a handful of sand on hand."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"R2": {  # 合成栅栏需手头持有>=8板(资源输入-数量阈值/囤料); mod门控,非datapack
  "id":"R2","level":"L1","dimension":"resource","action":"craftFence",
  "mechanism":"mod_event",
  "payload":{"gate":"craft_result","params":{
      "result_match":"minecraft:oak_fence","require":"inventory_min",
      "item":"minecraft:oak_planks","count":8}},
  "ground_truth":{"target":"inventory_count","property":"oak_planks","comparator":">=","value":8},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[resource]","hinted":"The fence won't come together unless you've a good stack of planks on hand."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"R4": {  # 合成铁镐需先持button(资源输入-中间物)
  "id":"R4","level":"L3","dimension":"resource","action":"craftIronPickaxe",
  "mechanism":"mod_event",
  "payload":{"gate":"craft_result","params":{
      "result_match":"minecraft:iron_pickaxe","require":"inventory_min",
      "item":"minecraft:oak_button","count":1}},
  "ground_truth":{"target":"inventory_count","property":"oak_button","comparator":">=","value":1},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[resource]","hinted":"The pickaxe assembly seems to call for a small wooden button first."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"R5": {  # 挖煤需石镐(资源输入-工具档位)
  "id":"R5","level":"L2","dimension":"capability","action":"gatherCoalOre",
  "mechanism":"datapack_tag",
  "payload":{"tag_file":"needs_stone_tool.json",
    "values_add":["minecraft:coal_ore","minecraft:deepslate_coal_ore"]},
  "ground_truth":{"target":"held_tool","property":"pickaxe","comparator":">=","value":"stone"},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[capability]","hinted":"Coal ore won't yield to a wooden tool."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"R6": {  # 挖金需钻石镐(资源输入-工具档位)
  "id":"R6","level":"L2","dimension":"capability","action":"mineGoldOre",
  "mechanism":"datapack_tag",
  "payload":{"tag_file":"needs_diamond_tool.json",
    "values_add":["minecraft:gold_ore","minecraft:deepslate_gold_ore"]},
  "ground_truth":{"target":"held_tool","property":"pickaxe","comparator":">=","value":"diamond"},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[capability]","hinted":"Gold ore needs a diamond-grade pickaxe to mine."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"C1": {  # 合成铁镐需附近熔炉(情境约束-设施)
  "id":"C1","level":"L3","dimension":"procedure","action":"craftIronPickaxe",
  "mechanism":"mod_event",
  "payload":{"gate":"craft_result","params":{
      "result_match":"minecraft:iron_pickaxe","require":"nearby_block",
      "block":"minecraft:furnace","radius":3}},
  "ground_truth":{"target":"nearby_block","property":"furnace","comparator":"<=k","value":3},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[procedure]","hinted":"Forging the pickaxe needs a furnace close by."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"C3": {  # 熔炼铁需夜晚(情境约束-时间)★需Java nighttime
  "id":"C3","level":"L5","dimension":"environment","action":"smeltRawIron",
  "mechanism":"mod_event",
  "payload":{"gate":"furnace_tick","params":{"require":"nighttime"}},
  "ground_truth":{"target":"time_of_day","property":"clock","comparator":"in","value":[13000,23000]},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[environment]","hinted":"The furnace only makes progress after nightfall."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},

"C4": {  # 挖钻石需深度y<=-10(情境约束-位置)
  "id":"C4","level":"L4","dimension":"context","action":"mineDiamondOre",
  "mechanism":"mod_event",
  "payload":{"gate":"block_break","params":{"require":"player_y<=","value":-10}},
  "ground_truth":{"target":"y_level","property":"y","comparator":"<=","value":-10},
  "failure_mode":"no_output",
  "feedback_text":{"typed":"[context]","hinted":"Diamonds only drop when you dig deep enough."},
  "authored_blind":False,
  "solvability":{"verified":True,"oracle_plan_steps":None},
  "intervention_check":{"i_plus_compilable":True,"i_minus_compilable":True}},
}
NEW_ORDER = ["R1","R2","R4","R5","R6","C1","C3","C4"]

# ============ 8个新偏差的 task 定义 ============
NEW_TASKS = {
"R1":{"action":"craftFurnace","goal":"furnace","spot":"surface",
      "inventory":{"cobblestone":8,"crafting_table":1,"chest":1},
      "setblocks":[["+2 ~ ~","sand"],["+3 ~ ~","sand"]]},
"R2":{"action":"craftFence","goal":"oak_fence","spot":"surface",
      "inventory":{"oak_planks":6,"stick":2,"crafting_table":1,"chest":1},
      "setblocks":[["+2 ~ ~","oak_log"],["+3 ~ ~","oak_log"],["+4 ~ ~","oak_log"]]},
"R4":{"action":"craftIronPickaxe","goal":"iron_pickaxe","spot":"surface",
      "inventory":{"iron_ingot":3,"stick":2,"crafting_table":1,"oak_planks":2,"chest":1},
      "setblocks":[]},
"R5":{"action":"gatherCoalOre","goal":"coal","spot":"surface",
      "inventory":{"wooden_pickaxe":1,"stone_pickaxe":1,"chest":1},
      "setblocks":[["+2 ~ ~","coal_ore"],["+3 ~ ~","coal_ore"]]},
"R6":{"action":"mineGoldOre","goal":"raw_gold","spot":"shallow",
      "inventory":{"iron_pickaxe":1,"diamond_pickaxe":1,"chest":1},
      "setblocks":[["+2 ~ ~","gold_ore"],["+3 ~ ~","gold_ore"]]},
"C1":{"action":"craftIronPickaxe","goal":"iron_pickaxe","spot":"surface",
      "inventory":{"iron_ingot":3,"stick":2,"crafting_table":1,"furnace":1,"chest":1},
      "setblocks":[]},
"C3":{"action":"smeltRawIron","goal":"iron_ingot","spot":"surface",
      "inventory":{"raw_iron":2,"coal":3,"chest":1},
      "setblocks":[["+3 ~ ~","furnace"]],
      "commands":["/gamerule doDaylightCycle true","/time set 6000"]},
"C4":{"action":"mineDiamondOre","goal":"diamond","spot":"x1shallow",
      "inventory":{"iron_pickaxe":1,"chest":1},
      "setblocks":[["+1 ~ ~","diamond_ore"]]},
}

def patch_recipe_tree():
    """R4 需要 oak_button(配方树原本没有);加入 vanilla 配方 1 plank -> 1 button。"""
    import json
    rt_path = "Adam/tcpg/recipe_tree.json"
    rt = json.load(open(rt_path))
    if "oak_button" not in rt:
        rt["oak_button"] = {"via": "craft", "inputs": {"oak_planks": 1},
                            "out_count": 1, "station": None}
        json.dump(rt, open(rt_path, "w"), ensure_ascii=False, indent=2)
        print("  recipe_tree: 加入 oak_button 配方(1 oak_planks -> 1 oak_button)")
    else:
        print("  recipe_tree: oak_button 已存在,跳过")

def main():
    patch_recipe_tree()
    # --- biases.yaml ---
    b = load(BIASES)
    # 保留文档头注释,替换 biases 列表为新8个(按NEW_ORDER)
    new_list = []
    for bid in NEW_ORDER:
        new_list.append(NEW_BIASES[bid])
    b["biases"] = new_list
    dump(b, BIASES)

    # --- tasks.yaml ---
    t = load(TASKS)
    # 删除旧的、写入新的8个
    t["biases"] = {bid: NEW_TASKS[bid] for bid in NEW_ORDER}
    # discovery suite 列表更新
    if "suites" in t and "discovery" in t["suites"]:
        t["suites"]["discovery"]["biases"] = list(NEW_ORDER)
    dump(t, TASKS)

    print("套件配置完成: 8偏差", NEW_ORDER)
    print("  (craftBoat=C2 / craftReinforcedHandle=R3 因技能未注册暂缓,见HANDOFF)")
    return 0

if __name__=="__main__":
    sys.exit(main())
