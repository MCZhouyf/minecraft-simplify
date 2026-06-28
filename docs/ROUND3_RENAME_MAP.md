# 第三轮套件重命名映射表（权威参照）

## 两大类划分依据
按"环境施加的前提约束动作的哪个侧面"二分：
- 资源输入型：约束"动作需要提供什么"（携带的物料/工具/中间产物）→ 对照可经取用/合成/装备低代价达成 → 模拟器可验证
- 情境约束型：约束"动作需在什么情况下执行"（所处时空/邻近实体）→ 对照需移动/等待/放置 → 须真实探索

## 映射表
| 新编号 | 原编号 | 大类 | 动作 | 隐藏规则 | 真因谓词 | 子维度 |
|---|---|---|---|---|---|---|
| R1 | R1 | 资源输入 | craftFurnace | 需催化剂砂 | inventory_count(sand)≥1 ∧ cobblestone≥7 | 物料-催化剂 |
| R2 | R2 | 资源输入 | craftFence | 需板材≥8 | inventory_count(oak_planks)≥8 | 物料-数量 |
| R3 | R3 | 资源输入 | craft reinforced_handle | 伪新物品配方 | stick≥3 ∧ cobblestone≥2 | 物料-未知配方 |
| R4 | P1 | 资源输入 | craftIronPickaxe | 需先持有button | inventory_has(wooden_button) | 物料-中间物 |
| R5 | C1 | 资源输入 | gatherCoalOre | 需石镐 | held_tool_tier≥stone | 工具-档位 |
| R6 | C2 | 资源输入 | mineGoldOre | 需钻石镐 | held_tool_tier≥diamond | 工具-档位 |
| C1 | P2 | 情境约束 | craftIronPickaxe | 需附近熔炉 | nearby_block(furnace,3) | 设施-邻近 |
| C2 | E3(新) | 情境约束 | craftBoat | 需附近水 | nearby_block(water,3) | 设施-环境物 |
| C3 | E1 | 情境约束 | smeltRawIron | 需夜晚 | time_of_day∈[13000,23000] | 时空-时间 |
| C4 | E2/X1 | 情境约束 | mineDiamondOre | 需深度y≤-10 | target_block_y≤-10 | 时空-位置 |

## 弃用
- 原 X2（采沙需手持铲）：执行器自动装备工具，无法构造稳定 unsat。弃用。
- 原 E2（合成需见天 sky_exposed）：LLM 盲区+地下spot冲突。弃用（C2 改用"近水"替代环境类）。

## 落地所需改动（后续阶段，非本阶段）
- 不改 mod 的 9 个：R1 R2 R4 R5 R6 C1 C2 C3(注入参数) C4 → 改 tasks.yaml + biases.yaml
- 需写 datapack：R3（reinforced_handle 新物品+配方+类别tag）
- 需改 Java（1行 nighttime）：C3 夜晚 → GateConfig 加 case "nighttime"
