# 预算感知的干预验证（成本敏感 acquisition + K5 不可满足 wait 守卫）

基于 commit 169d11b（X1 降本已合入，离线基线 102 passed）。

## 背景
第二轮跑批（gpt-5.1）暴露：X1 真因 y_level 已被 LLM 正确提出并进池，但 acquisition 反复
优先验证高成本候选（diamond_pickaxe 合成 + time_of_day 全域 wait），导致 craftItem
"No crafting table nearby" 与 HTTP 180s 超时。根因（代码核实）：当前 select 打分
score=q̂·γ_plus **完全不含代价**——而作者老版本论文（paper6_0603）的成本约束设计本有
Score=q̂·ΔE/c(h) 与预算约束 c(h)≤B_t，当前实现把代价项丢了。本修复**恢复**该代价项。

## 关键非领域定制性
编译器 K5 对每个候选**本就计算 est_steps**（cost_table.json + compiler），已存于
self.k5[cid]["est_steps"] 并记入 K7 compile 事件——只是 select 无视了它。所以代价来源是
签名驱动编译器的既有产物，**无任何"X1 是数值边界"的任务特判**（不按偏差特判、不屏蔽
ordered-domain 邻域；邻域照常生成，只是贵的候选靠后验证或暂不进预算）。

## 三项改动（最小、忠于老论文）
### 1. 成本敏感 acquisition（posterior.py）
Acquisition.select 接受 per-cid costs + budget；打分改为
  score = q̂·γ_plus / (c(h)+c0)^alpha
- alpha=0 或 costs=None → 退化为原 q̂·γ（成本无视，向后兼容，可复现旧行为）。
- alpha 默认 0.5（成本平方根惩罚，温和偏好廉价候选）；c0=1.0 平滑常数。
- 保留 round-robin 兜底（防贪心饿死弱候选）。
- budget 给定时，仅 c(h)≤budget 的候选进入 eligible；若都不可负担则回退全集（不停摆）。

### 2. 每触发预算（runtime.py）
_verification_loop 维护每触发 budget（cfg.trigger_budget，缺省 0=不限），每次干预后按
est_steps 扣减；costs 从 self.k5[cid]["est_steps"] 取，传入 select。与 max_iv 协同取更紧者。

### 3. K5 不可满足 wait 守卫（compiler.py）
_t_time_of_day：若时间窗 [a,b] 覆盖全天（b-a≥24000 或 a≤0∧b≥24000），I-（wait until_out）
永远不可完成——judge Infeasible(no_macro)，不进验证队列（修 time_of_day 全域 wait 的
180s 超时）。正常窗口（如 [0,12000]）照常编译。符合 K5"对任何候选返回显式判定、不崩"契约。

## 配置（runtime CONFIG_DEFAULTS 新增）
cost_alpha=0.5, cost_c0=1.0, trigger_budget=0.0（=不限；消融时可设具体值）。

## 变更文件
- Adam/tcpg/posterior.py（Acquisition 成本敏感 select + DEFAULTS）
- Adam/tcpg/runtime.py（每触发预算 + 传 costs + Acquisition 构造参数 + CONFIG_DEFAULTS）
- Adam/tcpg/compiler.py（_t_time_of_day 全天窗口判 no_macro）
- tests/test_budget_aware_acquisition.py（新增 7 用例）
- docs/BUDGET_AWARE_ACQUISITION_HANDOFF.md（新增）
不动：ccg.py / proposer.py / executor.py / evaluate.py / biases.yaml / tasks.yaml / injection_probe.py。
**未改双池后验/写回判据/neighbor 扩展/K6 主循环结构。**

## 测试
离线全量 **109 passed**（102 + 7 新）。新用例验证：
- 证据相同时成本敏感优先选廉价候选；budget 排除超预算候选；都不可负担不停摆。
- alpha=0 成本无视（向后兼容）；round-robin 兜底仍触发。
- 全天 wait 窗口 → no_macro；正常窗口照常编译。

## 对 X1 的预期
- y_level 干预便宜（挪几格）→ 优先验证、便宜命中。
- diamond_pickaxe 干预贵（合成）→ 靠后或超预算暂不选。
- time_of_day 全域 wait → no_macro，根本不进队列。
gpt-5.1 提出的正确 y_level 有望被优先验证、X1 从"边界"转"成功"——全由通用成本敏感调度达成。

## 验收
- 离线 109 passed。
- **X1 重跑（需开 LAN）**：真实 LLM，预期不再卡 diamond_pickaxe 合成 / time wait 超时；
  y_level 应被优先验证；X1 有望 gt_accepted=1（若 LLM 稳定提 y_level）。
- 消融建议：cost_alpha ∈ {0, 0.5, 1} 三档对比（0=复现卡死，0.5/1=命中），作为成本意识价值的证据。

## 论文写作
- 4.4 节获取准则写成 q̂·γ_plus/c^alpha，三项对应"置信×收益/代价"，引用老论文代价表标定（附录 C 同口径）。
- 4.5 节复用老论文定理 1 的 N_h*=⌊(B_cum−B_warm)/c*⌋，说明成本敏感调度固定预算下样本效率更优。
- K5 全天 wait 判 no_macro 作为编译器健壮性契约（B.7）又一实例。
