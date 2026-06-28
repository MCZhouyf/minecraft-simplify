# 第二轮·阶段2 交付：套件诚实重构（改配置 + 注入有效性探针）

基于 commit c59f401（阶段1已合入，离线基线 98 passed）。本阶段改 biases.yaml / tasks.yaml + 新增注入探针，**无 Java、无 mod 重建**——关键发现：所有改动都能在 Python 侧完成。

## 关键技术判断（决定了重构方式）

**X2 的真正问题不是 mod 缺门控，是任务配置 bug。** 第一轮 X2 注入完全无效（6/6 直接成功）的根因：X2 起始物资是 `{wooden_shovel:1, chest:1}`——**bot 一开始就手持铲子**，held_match 门控永远 pass，动作永不失败。mod 端 block_break + held_match 门控代码（GateConfig.java）本身是好的、有审计日志的。所以 X2 不需要换机制、不需要写 block_below（mod 没有该门控类型，写它要改 Java + Gradle 编译，容器做不了）。**修法**：起始改为手持 dirt（`equip: dirt`）、铲子留在背包——空手（拿 dirt）挖沙失败，干预 do(held_item=shovel)=装备铲子成功。复用 runner 已支持的 equip 字段，零 runner/mod 改动。

**E1 窗口无法加宽**（mod 的 daytime 硬编码 <12000，加宽要改 Java）。改为**让干预更便宜**：起始时间设到 11000（接近正午边界），等待到白天的宏只需短暂推进。机制与真值 [0,12000] 不变。

## 改动清单

### biases.yaml（mc_drift/biases/biases.yaml）
| 偏差 | 改动 | payload + ground_truth 同步 |
|---|---|---|
| X1 | 深度 −32 → **−10**（降干预代价） | payload player_y<= −10 + GT y_level<=−10 |
| R2 | 材料 flint(概率掉落) → **coal**(确定性) | payload item coal + GT inventory_count(coal) |
其余偏差 biases.yaml 不变。solvability 已重新验证 + 回填（12/12 verified，循环偏差仍被拒，R2 oracle 154 步）。

### tasks.yaml（experiments/tasks.yaml）
- **X2**：起始 `{wooden_shovel:1, dirt:1, chest:1}` + `equip: dirt`（手持非铲子→门控可触发失败）。
- **X1**：新增锚点 `x1shallow: [0,-6,0]`，X1 用它（起始 y=−6，下挖到 ≤−10 只需几格，do(y_level) 便宜）。
- **E1**：`/time set 18000` → `/time set 11000`（接近白天边界，等待便宜）。
- **R2**：起始加 wooden_pickaxe + 改放 coal_ore（配合 coal 真因，可采集煤）。
- **P2 移出 discovery 套件**（station_base_block 冷僻，转阶段5原语准入专项）。discovery 套件从 12 → 11 个偏差。

### 新增：注入有效性探针（experiments/injection_probe.py）
这是第一轮缺失、导致 X2 脏数据的检查。对每个偏差做 2 点测试：**真因不满足→必须失败、真因满足→必须成功**。任一偏差"不满足却成功"即注入失效，禁止进跑批。这是阶段4跑批前的强制门禁。

## 重构后主套件（11 个，五维全覆盖）
| 维度 | 偏差 | 数量 |
|---|---|---|
| capability | C1 C2 C3 | 3 |
| resource | R1 R2(coal) R3 | 3 |
| procedure | P1 | 1（P2→准入专项） |
| context | X1(−10) X2(equip dirt) | 2 |
| environment | E1(近边界) E2 | 2 |

## 变更文件
- mc_drift/biases/biases.yaml（X1 深度、R2 材料、solvability 回填）
- experiments/tasks.yaml（X2/X1/E1/R2 任务、P2 移出、x1shallow 锚点）
- experiments/injection_probe.py（新增）
- docs/ROUND2_STAGE2_HANDOFF.md（新增）
不动：Adam/ 任何代码、mc_drift/*.py、runner.py、evaluate.py、所有测试。

## 验收
- 离线全量 **98 passed**（套件改动不碰逻辑，测试数不变）。
- solvability --all：12/12 verified + 循环偏差被拒。
- **注入探针（需开 LAN）**：`IAP_MC_PORT=... python3 experiments/injection_probe.py --all` → 全部 VALID。**X2 必须在这里从 INVALID 变 VALID**，否则说明 equip dirt 方案在 mineflayer 上仍不触发失败，需进一步查（可能 gatherSand 空手也能挖，则该偏差确需移除）。

## 给 Codex 的人工步骤提醒（不要代跑）
1. 阶段2改的是配置，离线测试和 solvability 可直接验。
2. 注入探针必须开世界开 LAN 后人工跑，这是阶段4跑批的前置门禁。
3. anchors 坐标（含新增 x1shallow）需按试验区实际地形确认可达。
