# 修复：干预执行事务化（undo 失败 → abort trigger + reset anchor）

基于 commit 9033f25（预算感知已合入，离线基线 109 passed）。这是确凿 bug，影响全矩阵
（任何偏差只要一次 undo 失败，后续候选都在污染状态下验证）。

## 背景（X1 重测暴露）
gpt-5.1 + budget-aware 后，X1 的 K7 显示：acquisition 先验证低成本非真因候选
（inventory_count/held_item），其 chest/equip 链路 undo 失败 → held=null、inventory={}；
但旧代码只 `pool.invalidate_last(); done+=1; continue`——作废观测后**在污染状态下继续验证
下一个候选**（包括后来的 y_level）。这是执行非事务化的缺陷。

## 设计原则（与论文 C3 一致）
干预后恢复的是**验证所需的可比上下文 C_t**（工具装卸、位置、库存这类可恢复维度），
**不是**声称逆转了不可逆地付出的代价（已消耗资源、已流逝时间）。代价不可逆地付出，
换来在可恢复上下文下的一次对照观测——两者不矛盾。若 anchor 无法恢复则仍 abort（不在
不确定状态下继续）。

## 修复内容
1. **捕获 episode anchor**：on_action 已取 full 快照（held/inventory/position），作为 anchor
   传入 _verification_loop。
2. **undo/ctx 失败 → abort + reset**：原 `invalidate_last; continue` 改为
   invalidate_last → _reset_to_anchor(anchor) → log trigger_abort → **return**（终止本次
   触发，不在污染状态下继续验证其他候选）。
3. **forward plan 失败 → abort + reset**：plan 部分执行后失败（放了 chest、存了工具再 error）
   同样污染状态，原"clean discard"实则不 clean；改为同样 abort + reset。
4. **_reset_to_anchor 助手**：从 anchor 重建 reset options（inventory/position/equipment），
   复用 freedo 已验证的 env.reset 状态直设机制；anchor 缺失或 reset 失败返回 False，调用方
   仍 abort。仅恢复可恢复维度，不退还已付代价。

## 变更文件
- Adam/tcpg/runtime.py（anchor 捕获 + 传参 + _reset_to_anchor + 两处 abort+reset）
- tests/test_transaction_reset.py（新增 3 用例）
- docs/FIX_TRANSACTION_RESET_HANDOFF.md（新增）
不动：posterior.py/ccg.py/compiler.py/proposer.py/executor.py/evaluate.py/biases.yaml/tasks.yaml。
未改 acquisition 打分、写回判据、双池逻辑。

## 测试
离线全量 **112 passed**（109 + 3 新）。新用例验证：
- undo 失败后 reset 到 anchor（held 恢复 iron_pickaxe 非 None、inventory 恢复）、emit trigger_abort、
  且不在污染状态继续。
- _reset_to_anchor 从 anchor 正确恢复 held/y/inventory。
- anchor=None 时安全返回 False、不崩。

## 对 X1 的预期
X1 重跑应不再出现"held=null/inventory={} 传播到 y_level 验证"。每次触发若有 undo 失败即
干净重置，后续候选在 anchor 上验证。注意：y_level 干预本身的 pathfinder 移动慢（343s/
GoalChanged）是另一个正交问题（位置类高代价干预），本修复不解决——X1 仍按"位置类高代价
干预边界"诚实报告。

## 验收
- 离线 112 passed。
- X1 重跑（需 LAN）：K7 应见 trigger_abort 事件、ctx_snapshot 不再 held=null 传播；
  若 y_level 被选中，其验证应在干净 anchor 上进行（move 慢仍属预期边界）。

## 给 Codex 的人工步骤提醒
重跑任一含 chest/equip 干预的偏差，K7 查 trigger_abort 事件与 anchor_restored=true；
确认后续 intervention_start 的 ctx_snapshot 不再出现 held=null/inventory={} 污染传播。
