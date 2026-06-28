# 修复：区分真撤销失败 vs ctx 漂移 + 真因保底验证（基于批次A真实数据）

基于当前 GitHub HEAD = bf9531c（产生批次A数据的代码）。离线基线 112 passed → 修复后 116 passed。

## 客观根因（逐 run K7 数据,非推测）
批次A tcpg 7 偏差仅 C2 成功。逐 run 提取真因候选的干预结局：

| 偏差 | 真因分到干预次数 | 结局 | 真因失败的真实原因 |
|---|---|---|---|
| C2 | 5 | 全 undo_ok=True ctx=True | （成功标杆）q̂ 0.56→0.95 写回 |
| P1 | 5 | 2次 undo_ok=True **ctx=False**、2次abort、1正常 | **ctx 误判吃掉有效观测** |
| R3 | 11 | 5次 ctx=False、3次abort、5正常,但 q̂ 在降 | ctx 误判 + 双池信号本身弱 |
| C1 | 1 | 1次正常,q̂=0.06 | 只验1次(抽象层级竞争抢先) |
| C3 | 4 | 3次abort(plan_fail/undo_fail) | 干预 plan 执行失败 |
| R1 | 0 | 真因 cid 空集 | **真因根本没进池(提案/编译层)** |

全批次 undo 分布：undo_ok=True&ctx=True 114；**undo_ok=True&ctx=False 60**；undo_ok=False 6。
即 66 次 abort 中 60 次是"撤销成功却被 ctx 复核判不一致"。

根因核实（读 _ctx_matches 源码）：第一个检查就是 `if snap.held.name != now.held.name: return False`。
held_tool 类干预会装备/卸下工具(held.name 改变)，撤销后 held.name 与干预前快照的表层差异
（工具实例/槽位）即触发 ctx=False → abort。这是 60 次误判的确切来源。

## 修复（两部分,精确对应根因）
### 1. 区分三种 undo 结局(取代原单一 `not(undo_ok and ctx_ok)` abort)
- **undo_ok=False（真污染,6例）**：作废观测 + reset anchor + abort（reason=undo_fail）。保留原保护。
- **undo_ok=True 但 ctx_ok=False（漂移,60例）**：观测 y2 在干预态下、撤销前取得,**有效**；漂移只
  威胁下一次观测。故 **reset_to_anchor 恢复可比上下文：成功→保留观测+继续(记 ctx_resync);
  失败→才 abort(reason=ctx_unrestorable)**。不再无谓 abort 60 次。
  ★严谨性：不是无脑保留,而是"确认上下文能恢复"才保留;不能恢复(真污染)仍 abort。
- 都 OK：正常。

### 2. 真因保底验证次数 min_verifications_per_cand（config,缺省0=关闭）
>0 时每轮优先选"验证次数未达保底"的候选,**绕过预算上限**(使昂贵真因如 held_tool>=stone
est_steps~110 不被 c(h)>budget 跳过),round-robin 序。保证昂贵真因不被成本排序饿死。

## 这次修复对每个偏差的预期效果（诚实,不夸大）
- **P1：对症,应改善**——被 ctx 误判吃掉的 2 次观测将保留,真因有机会积累 q̂。
- **R3：部分**——ctx 误判修复让数据更干净,但 q̂ 在降说明双池信号弱,可能仍不写回(需看重跑)。
- **C1：部分**——保底让真因被验多次,但"抽象层级竞争"(held_item抢先)本次未修,可能仍失败。
- **C3：部分**——保底有用,但 plan_fail(执行层)本次未修,可能仍失败。
- **R1：无效**——真因没进池是提案/编译层问题,不在本修复范围(需另查)。

**重要**：本修复不保证 5 个全绿。它确定改善 P1、提升数据质量,并把"被 abort 噪声掩盖的深层
问题"暴露出来——重跑后若 R3/C1 的 q̂ 仍上不去,即确认是双池信号问题(而非 abort),再分别处理。
一次修一类根因。

## 变更文件
- Adam/tcpg/runtime.py（验证循环 abort 分支三分 + 保底 + ctx_resync 事件 + config 默认）
- tests/test_abort_resync_floor.py（新增 4 用例,精确验证行为非仅"不崩"）
- docs/FIX_ABORT_RESYNC_FLOOR_HANDOFF.md（新增）
不动：posterior.py/ccg.py/compiler.py/proposer.py/executor.py/evaluate.py/biases.yaml/tasks.yaml。
未改成本感知公式、写回判据、双池逻辑、_ctx_matches 本身。

## 测试
离线 116 passed（112 + 4 新）。新用例验证：真 undo 失败仍 abort;ctx 漂移+reset成功→保留观测+
ctx_resync+posterior_update+不 abort;ctx 漂移+reset失败→ctx_unrestorable abort;
保底强制昂贵候选(cost100>budget5)至少验 2 次。

## 重跑建议（验证修复）
先单跑最对症的 P1 + 最典型的 C3，设保底：
  tasks.yaml defaults 加 min_verifications_per_cand: 2 + max_interventions_per_event: 10
  python3 experiments/runner.py --suite discovery --bias P1 --mode tcpg --seed 0 --no-viewer
查：K7 的 trigger_abort 大幅减少、出现 ctx_resync;P1 真因 q̂ 是否积累、是否写回。
P1 改善 → 重跑整个批次A;各偏差按上表预期对照,未达预期的按"剩余根因"单独处理。
