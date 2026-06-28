# 修复：X1 工程降本（归入诚实边界，问题 B）

基于 commit 84e07d2（阶段3已合入，离线基线 102 passed）。

## 背景（阶段3反馈的处置决策）
阶段3后 Codex 反馈两件事：freedo 降本已生效（scripted X1 能 writeback y_level=-10），
但真实 LLM 对 X1 不稳定提出 y_level，且 mineDiamondOre retry 每次 45-60s 导致 full run 过贵。

决策（已确认）：
- **问题 A（LLM 不提 y_level）**：X1 真因是 y_level 数值阈值，不在 schema 有序枚举里，
  neighbor 扩展按设计不补它——这是论文局限 vii 已诚实记录的数值盲区。**不为 X1 加方法**
  （不上差异引导），X1 作为"数值阈值类偏差、发现依赖 LLM 提案质量"的诚实边界案例报告。
  context 维度由 X2 兜底。
- **问题 B（retry 太慢）**：纯工程，与 LLM 无关。本修复处理。

## 修复内容（只降成本，不改任何主逻辑）
mineDiamondOre 动作本身慢（mineflayer 挖钻石 45-60s）无法消除，故降低 retry 次数与寻路：
1. **per-bias 预算覆盖**（runner）：run_one 现读取 task 级的 episodes / step_budget /
   max_interventions_per_event，缺省回落 defaults。这是通用机制，其他偏差不受影响。
2. **X1 预算下调**（tasks.yaml）：episodes 6→4、max_interventions_per_event 默认6→3。
   估算 X1 full run 从"过高"降到 ~13min（4 ep × 4 次动作 × 50s），远低于其 45min 时限。
3. **矿点更近**（tasks.yaml）：ore +2 → +1（贴邻 bot），减少每次 retry 的寻路。

## 变更文件
- experiments/runner.py（run_one 读 per-bias episodes/budget/max_iv 覆盖）
- experiments/tasks.yaml（X1 加 episodes:4 / max_interventions_per_event:3 / ore +1）
- docs/FIX_X1_COSTDOWN_HANDOFF.md（新增）
不动：Adam/ 任何代码、mc_drift/、evaluate.py、injection_probe.py、biases.yaml、所有测试。
**未改 K5/K6 主逻辑**（按 Codex 要求）。

## 测试
- 离线全量 **102 passed**（改动不碰逻辑，测试数不变）。
- 确认其他偏差（C2/R1/E2/P1）无 episode 覆盖、回落 defaults=6。

## 验收
- 离线 102 passed。
- **X1 full run（需开 LAN）**：真实 LLM 重跑 X1，预期 full run 在 ~15min 内完成、
  不超时、aborted=false。注意：X1 是诚实边界案例——**即使真因未发现（LLM 没提 y_level）
  也属预期**，本修复只保证它"跑得动且不拖垮矩阵"，不保证发现真因。
- 论文处理：X1 在结果表中作为数值阈值边界案例，gt_accepted 可能为 0，配合局限 vii 说明
  （数值真因依赖 LLM 提案，neighbor 扩展只覆盖有序枚举）。

## 给 Codex 的人工步骤提醒（不要代跑）
重跑 X1（真实 LLM）：
  IAP_MC_PORT=... python3 experiments/runner.py --suite discovery --bias X1 --mode tcpg --seed 0
预期：~15min 内完成、aborted=false。真因未发现属正常（诚实边界），重点是 run 能跑完不卡死。
