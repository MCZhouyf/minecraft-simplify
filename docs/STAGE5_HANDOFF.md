# 第 5 阶段交付与执行指引（干预编译器 + 可解性验证器）

## 本次交付的文件
| 文件 | 状态 | 说明 |
|---|---|---|
| Adam/tcpg/recipe_tree.json | 覆盖 | 套件物品链静态机制模型（原版语义；偏差修改由 solvability 注入到副本） |
| Adam/tcpg/cost_table.json | 覆盖 | 步数估计（单位代价 + 按方块的采集摊销；标注 ESTIMATE，只进可行性/效率核算，不进最优性主张） |
| Adam/tcpg/compiler.py | 覆盖 | **IaP 归约的实现核心**：compile(candidate, state)→K5 纯数据计划（plan±/undo±/est/irreversible）；9 个模板 + acquire_plan 配方树递归（库存抵扣 + 环检测）；暂存纪律（do(h=0) 用 useChest 存取，永不销毁物品）；admitted 单比较表达式翻译为核心等价候选；dry_run；INFEASIBLE 四原因 |
| Adam/tcpg/executor.py | 覆盖 | K5→JS 渲染（每原语一个 env.step，独立 K7 事件 + 干净弃用路径）；直接读 control_primitives/*.js（不经 skill_loader，离线可导入）；equip/moveTo/wait/roof 为内联 JS（pathfinder GoalY/GoalXZ、时间轮询、顶柱挖/封） |
| mc_drift/solvability.py | 覆盖 | INV-1 验证器：每偏差在"注入后机制模型"上验证正例可达（≤500 步）+ 真值候选 I±  dry-run；--write 回填 biases.yaml（注释保持）；E.3 哨兵——合成循环偏差"钻石矿需钻石镐"必须被拒 |
| mc_drift/biases/biases.yaml | 覆盖 | **已回填**：12 条 verified: true + oracle_plan_steps（6~331）+ i±_compilable: true |
| tests/test_compiler.py | 新增 | 11 用例：递归/抵扣/环检测、各模板结构断言、暂存纪律、不可逆与不可干预、步数上限与单调性、全原语 JS 渲染 |
| tests/test_solvability.py | 新增 | 5 用例：12/12 可解、C1 浅层见证语义、循环拒绝、报告与退出码、yaml 回填保结构 |
| tests/integration/test_compiler_inworld.py | 新增 | 3 个活体往返：箱存取（物品不销毁）/ 装备切换 / y 层下行返回——每个都经 K3 谓词断言翻转 |

离线全量在交付机已验证：**69 passed**（53 旧 + 16 新）；solvability --all --write 退出码 0。

## 本阶段两个值得注意的结果
1. **C1 的浅层见证语义**（开发中实际抓到的一个建模错误）：C1 只 tag deepslate_iron_ore，浅层 iron_ore 不受影响——深层被门控、浅层留作可解性见证，这正是能力类偏差可解性保持的论证本身。验证器若把基础方块一并提级会产生假循环；现已精确匹配并写成专门测试钉死。
2. **oracle_plan_steps 已产出**（论文 E.2 配置示例的 ___ 填空）：R1=69 R2=139 R3=30 C1=42 C2=331 C3=38 P1=163 P2=232 X1=184 X2=18 E1=78 E2=6（估计值口径，附录 C）。

## 执行顺序
1. `pytest tests -m "not integration" -q` → 69 passed。
2. `python3 mc_drift/solvability.py --all` → 12 OK + circular rejected，退出码 0（biases.yaml 已随包回填，无需再 --write）。
3. 开世界开 LAN → export IAP_MC_PORT → 调整 test_compiler_inworld.py 顶部两个坐标。
4. `pytest tests/integration/test_compiler_inworld.py -x`（3 用例约 4–6 分钟；y 层用例含寻路挖掘，最慢）。
5. 提交：`git add -A && git commit -m "stage5: intervention compiler (K5) + executor + solvability verifier (INV-1)" && git tag v0.5`。

## 设计要点与已知坑
- 计划是纯数据，生成与执行严格分离；executor 每原语一个 /step——任一步失败 run_plan 返回 False，**调用方必须作废本次观测**（候选保持未决，池不污染），这是第 6 阶段闭环的硬契约。
- block_below 与 ingredient_type 编译为 no_macro（observe-only）——当前 12 偏差无一需要它们的 I±，论文 B.2 表中这两行的 I± 列应标注"—（观测）"。
- nearby_block 仅站类方块（furnace/crafting_table/chest）可编译 I+（合成+放置）；矿石类目标静态不可编译（no_macro）属设计内。
- y 层移动用 pathfinder GoalY + canDig（阶梯式），integration 用例 DEEP_SPOT 别选基岩附近；moveTo 超时会作为步骤失败走弃用路径，属预期。
- wait 宏上限 70×200=14000 tick；E1 类干预的真实等待很贵，第 6 阶段闭环在启动干预前先查剩余步数预算。
