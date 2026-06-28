# 第二轮·阶段3 交付：干预降本（freedo 真·状态直设 + ctx 复核扩展 + 时限分级）

基于 commit 6a08d2c（阶段2 + F3m 修复已合入，离线基线 98 passed）。本阶段改 runtime.py + runner.py，离线可验。

## 三项改动

### 1. freedo 真·状态直设（救 X1 超时，最核心）
**问题**：第一轮 freedo 把 moveTo/wait 用 run_plan **在世界里真实执行**——这让 freedo 比 tcpg 还慢，X1 的 moveTo y=22 在世界里寻路超时 180s 崩溃。freedo 本应是"零成本 oracle 干预"。
**修法**：_freedo 重写——按计划的净效果分类实现，全部零交互成本：
- inventory/held/**position** → reset options（position 直接设 y，这是 X1 修复：不再世界内寻路）。
- **time_of_day** → /time set 命令（取窗口中点 tick，不等待）。
- **sky_exposed** → /setblock 命令（盖/开顶，不挖掘）。
- 仅真正无法实现的原语落入 passthrough（套件的 I+/I- 不应触发）。
新增 _freedo_commands 用单次 env.step(chat) 下发 /time、/setblock，不计交互成本。
**字段已核对**：JS state_snapshot 真实返回 agent.x/y/z、world.time_of_day、sky_exposed、held.name/tier——_freedo 与 ctx 用的字段名全部匹配真实快照。

### 2. ctx 复核扩展到门控相关字段（救 C1 负池污染）
**问题**：C1 负池本该全 0 却混入成功观测，提示 ctx 复核太松——只查了 held.name + agent.y。
**修法**：_ctx_matches 增加候选门控字段的复核。对正在验证的候选，按其 target 取对应快照字段（y_level→agent.y、time_of_day→world.time_of_day、sky_exposed→sky_exposed、held_tool/item→held.name），undo 后该字段若未恢复（超容差：y±3、time±2000），观测作废。这收紧了 C 类负池——门控维度的残留漂移不再污染对照。_snapshot 现额外携带 _full 全快照供门控字段读取。

### 3. 单 run 时限按 oracle 步数分级（防止单 run 挂死矩阵）
**问题**：第一轮无 per-run 时限，长 run 靠人工 25 分钟杀。
**修法**：runner 按偏差 oracle 步数（biases.yaml 的 solvability.oracle_plan_steps）设时限：≤50 步→20min，≤150→30min，>150→45min。超时优雅中止并在 summary 记 aborted=true，不挂死。新增 _bias_oracle_steps 从 biases.yaml 读。

## 变更文件
- Adam/tcpg/runtime.py（_freedo 重写 + _freedo_commands + _ctx_matches 扩展 + _gate_field/_gate_tol + _snapshot 带 _full）
- experiments/runner.py（_bias_oracle_steps + 时限分级 + summary 记 time_budget_s/aborted）
- tests/test_freedo_costdown.py（新增 4 用例）
- docs/ROUND2_STAGE3_HANDOFF.md（新增）
不动：proposer.py / posterior.py / ccg.py / compiler.py / executor.py / evaluate.py / biases.yaml / tasks.yaml / injection_probe.py。

## 测试
离线全量 **102 passed**（98 + 4 新 freedo 用例）。新用例验证：y_level 经 reset 设位置（**零 in-world run_plan**）、time 经 /time set、sky 经 /setblock、inventory/equip 经 reset——全部不在世界内跑长计划。

## 验收
- 离线 `pytest -m "not integration"` → 102 passed。
- **X1 freedo 单点（需开 LAN，关键验证）**：重跑 X1 的 freedo_oracle 单点，应**不再 moveTo 超时**、单 run 在时限内完成、6 回合齐。
- C1 单点：负池不再混入成功观测（k7 的 posterior_update 里 stone_pickaxe 对照下 k_neg 接近 0）。

## 给 Codex 的人工步骤提醒（不要代跑）
1. 离线 102 passed 后提交。
2. 开 LAN 后单点验证 freedo 降本：
   python3 experiments/runner.py --suite discovery --bias X1 --mode freedo_oracle --seed 0
   预期不超时、wall_seconds 远小于第一轮、aborted=false。
3. 单点 OK 后才进阶段4（全矩阵分批跑）。
