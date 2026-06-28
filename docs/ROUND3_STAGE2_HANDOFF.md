# 第三轮 阶段2 交接：套件配置一次到位（方案C）+ 模拟器验证代价分层

## 本阶段完成（容器三次查验，全新克隆应用后离线 118 passed）
旧12偏差重构为新8偏差，配置层一次到位，所有快照测试同步，
并加入资源输入型"受控配置验证"代价分层（论文4.4，模拟器验证的具身实现）。

## 套件（8偏差，schema合法）
资源输入型（"提供什么"）：
- R1 craftFurnace  需催化剂砂   inventory_count(sand)>=1      [mod_event craft_result+inventory_min]
- R2 craftFence    配方消耗8板   inventory_count(oak_planks)>=8 [datapack_recipe 配方变更]
- R4 craftIronPickaxe 需先持button inventory_count(oak_button)>=1 [mod_event inventory_min]
- R5 gatherCoalOre 需石镐      held_tool>=stone             [datapack_tag]
- R6 mineGoldOre   需钻石镐     held_tool>=diamond           [datapack_tag]
情境约束型（"在什么情况下"）：
- C1 craftIronPickaxe 需附近熔炉 nearby_block(furnace)<=3     [mod_event craft_result+nearby_block]
- C3 smeltRawIron  需夜晚      time_of_day in[13000,23000]  [mod_event furnace_tick+nighttime ★需Java]
- C4 mineDiamondOre 需深度    y_level<=-10                 [mod_event block_break+player_y]

注：dimension 字段用 schema 合法旧值(resource/capability/procedure/context/environment)；
"资源输入/情境约束"新命名仅在评测叙事/文档层，代码 schema 不变。

## 模拟器验证代价分层（论文4.4，本阶段加入）
- compiler.py: compile() 返回新增 sim_verifiable 字段——target∈{inventory_count,held_tool,
  held_item}(资源输入型)为True。
- runtime.py: 获取准则 cost(h) 分层——sim_verifiable 候选用低常数 sim_verify_cost(默认2.0)，
  对应"对照可经受控配置(take/craft/equip)低代价达成"；情境约束型保持 est_steps(真实探索)。
- 这是 ADAM 经受控初始配置隔离单变量的具身实现(非环境重置；重置式仍为 Free-do Oracle)。

## 两个偏差推迟（技能未注册，不阻塞本阶段8个）
- R3 craftReinforcedHandle(伪新物品)：动作未注册+需datapack定义新物品。待技能开发。
- C2 craftBoat(近水)：动作未注册，需写mineflayer技能。待技能开发。

## C3夜晚需Java改动（单独做，不阻塞其他7个）
furnace_tick 门控当前只支持 daytime。C3 需 nighttime：
GateConfig.java allowFurnaceTick 的 require switch 增加：
    case "nighttime" -> (world.getTimeOfDay() % 24000L) >= 12000L;
然后 ./gradlew build 重建 jar。C3 task 已设 commands:[/gamerule doDaylightCycle true,/time set 6000]。
Java 改好前 C3 跑批注入会失效，故 C3 真机验证排在 Java 改动之后。

## 本阶段改动文件（11个）
配置：mc_drift/biases/biases.yaml、experiments/tasks.yaml、Adam/tcpg/recipe_tree.json(加oak_button)
代码：Adam/tcpg/compiler.py(sim_verifiable)、Adam/tcpg/runtime.py(代价分层)
测试：tests/{test_datapack_gen,test_solvability,test_evaluate_offline,test_round2_bugfixes,test_compiler}.py
脚本存档：scripts/build_suite.py(生成本配置的依据，幂等)

## 应用方式
解压覆盖到仓库根目录(已是改好的完整文件，无需打补丁)。
应用后必须：python3 -m pytest tests -m "not integration" -q  → 118 passed。

## 下一步（真机跑批，逐个验证）
1. 先验7个不需Java的(R1 R2 R4 R5 R6 C1 C4)，从最干净的R2起。
2. 每个用方案B sanity 从真实数据验注入有效(无探针)。
3. 重测每个 oracle_plan_steps(当前置None，真机跑出真值回填)。
4. C3 待 Java nighttime 改好后单独验。R3/C2 待技能开发后加入。
