# 方案 B：弃用探针硬门禁，改由 sanity_check 从真实跑批数据检测注入失效

基于 commit 2ffe8a8（X2 已移出，离线 112 passed）。

## 背景与决策
独立注入探针（injection_probe.py）作为"跑批前必须全 VALID"的硬门禁，实践中误报多于命中：
3 个 INVALID 里 2 个（X1/E2）是探针自身构造 bug（sat leg 放错矿/给料不足），只有 1 个
（X2）是真问题。探针要在真实环境精确复现 unsat/sat 两态，本身和跑批一样易错，sat leg 尤其脆弱。
第一轮（cbd9994）并无探针，靠跑后分析筛掉失效偏差即可工作。

**决策（方案 B）**：不再用探针做硬门禁，直接进跑批；注入有效性由 sanity_check 从**真实跑批数据**
检测——某 discovery 偏差的 tcpg 自然观测若全部成功，说明注入从未触发失败（X2 失效模式），
标记为 BLOCKING 并要求弃用该偏差数据。这把"注入有效性"判断从"事前脆弱构造"挪到"事中真实数据
验证"，不引入探针自身的构造 bug。

## 改动
### experiments/sanity_check.py（重写为数据驱动的有效性闸门）
新增/强化检查（全部从 runs/ 真实数据读取）：
1. **注入有效性（探针替代）**：discovery + tcpg 的 episodes 若 natural_success 全为 true →
   BLOCKING（注入失效，弃用该偏差，cf. X2）。退出码 1。
2. **真因三态**：写回 / 进池未决 / 从未提出（读 k7 的 proposal/neighbor_expand + candidates）。
3. **llm_writeback writeback 数 >0**（消融下限不得空转）。
4. **K7 事件完整性**（tcpg 须含 proposal/compile/intervention_start/retry/undo/posterior_update）。
5. **事务化统计**：trigger_abort 次数、aborted（超时）run。
6. **污染检测**：intervention_start 的 ctx_snapshot 出现 held=null。
退出码：有 BLOCKING（注入失效）返 1，否则 0。

### injection_probe.py
保留，但**不再作为跑批前置门禁**，仅作单偏差临时调试工具（如怀疑某偏差注入时手动 --bias 查）。

## 跑批流程变化（方案 B）
旧：第1步探针全 VALID → 才跑批。
新：**直接进跑批**（第0步前置确认后）；每批跑完跑 sanity_check，若报 INJECTION INVALID 则
该偏差数据弃用（从套件移除或标注），其余照常分析。X2 那种失效会被抓到（episodes 全成功），
但不再因探针 sat-leg 误报被卡。

## 变更文件
- experiments/sanity_check.py（重写）
- docs/PLANB_NO_PROBE_GATE_HANDOFF.md（新增）
不动：Adam/、mc_drift/、runner.py、evaluate.py、biases.yaml、tasks.yaml、injection_probe.py、所有测试。

## 验证
- 离线 112 passed（sanity_check 不在测试内，改动不影响）。
- 合成数据自测：注入失效偏差（episodes 全成功）被正确标 BLOCKING + 退出码 1；
  正常偏差（首 episode 失败触发、GT 写回）无 BLOCKING。

## 跑批纪律（方案 B 版）
- 直接进跑批，不跑探针门禁。
- 每批跑完：python3 experiments/sanity_check.py > sanity_批名.txt；
  若出现 "INJECTION INVALID" 或 "!! INJECTION-INVALID biases" → 该偏差数据弃用，报告用户。
- 其余纪律不变（runs/ 不删、每批备份+commit、连错3次记 failures.md、C2/混杂组门槛卡控）。
