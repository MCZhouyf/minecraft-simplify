# 第二轮·阶段1 交付：三个 bug 级修复（纯离线）

基于 commit cbd9994（离线基线 94 passed）。本阶段全部离线可验，不需要开 Minecraft。

## 修复内容

### 修复 1：evaluator 给 llm_writeback 正确计分（核心，否则论点对比表为空）
**问题**：llm_writeback 模式下，runtime 把候选直接写回 CCG（无验证，消融下限），但在写回后**提前返回、未把候选注册进候选池**——所以 summary.json 的 candidates 为空 `[]`，而 evaluate.py 只读 summary candidates，导致 llm_writeback 的 n_accepted 恒为 0。后果：论文命脉对比（tcpg 拒混杂 vs llm_writeback 错误接受混杂）的表格永远是空的。
**修法**（只改 evaluate.py，不动 runtime 行为）：
- load_runs 额外加载 ccg.json 到 s["_ccg"]。
- run_metrics 新增 _accepted_rejected(s)：llm_writeback 模式从 CCG 的 conditions 读"接受集"（它写回的所有门控即视为接受，正是无验证写回的语义），其余模式仍从 summary candidates 的 status 读。
- confound_rejected / confound_wrongly_accepted 改用 mode-aware 的接受/拒绝集计算。
**实测**：真实 C2_llm_writeback run 修复后 n_accepted 从 0→4、confound_wrongly_accepted=1、gt_accepted=0（写了 vanilla iron 等门控但没写对真因 diamond）——正是消融下限该有的行为。

### 修复 2：必要性方向不把已知 E_in 边误当新门控（修 F3m）
**问题**：F3m（E1 镜像混杂）里 tcpg 错误"接受"了 inventory_count(raw_iron)≥1、inventory_count(coal)≥1。它们 source=success_precondition，来自 assumed_preconds 的配方输入（E_in）。do(h=0) 移除铁/煤确实让熔炼失败，所以被判定为门控——但它们是 vanilla 配方必需输入、**本就在 E_in 里的已知边**，重新当"新发现门控"接受是冗余错误，污染混杂接受指标。
**修法**（改 ccg.py + runtime.py）：
- ccg 新增 is_known_input_edge(cand)：判断候选是否只是复述某动作 E_in 里已有的对象输入。
- write_back：若是已知输入边，记为 status="confirmed_known"，**不**加入 e_ca 新门控、**不**计为 accepted（确认已知边有效但非新发现）。
- runtime 写回处同步：accepted 决策若命中已知输入边，候选 status 设为 confirmed_known。
**保留的能力**：这不削弱必要性检验本身——它仍会测试假定前提；只是"确认一条已知 E_in 边"不再被错报为"发现一个新门控"。真正的新门控（如 time_of_day）不在 E_in 里，照常写回为 accepted。

### 修复 3：station_type 编译
已在 cbd9994 仓库中（_t_station_type + _STATION_RADIUS 已存在，grep 计数 5）。无需再改；若第二轮从干净分支起，确认包含。

## 变更文件
- experiments/evaluate.py（load_runs 读 ccg.json；run_metrics 的 _accepted_rejected + mode-aware confound 计分）
- Adam/tcpg/ccg.py（is_known_input_edge + write_back 的 confirmed_known 分支）
- Adam/tcpg/runtime.py（写回处同步 confirmed_known 状态）
- tests/test_round2_bugfixes.py（4 个新用例）
不动：proposer.py / posterior.py / compiler.py / executor.py / schema.json / ADAM.py / biases.yaml / tasks.yaml / runner.py。

## 测试
离线全量 **98 passed**（94 + 4 新）。新用例覆盖：
- 已知 E_in 边记为 confirmed_known、不入 e_ca；真新门控仍写回 accepted；F3m 场景不再误计混杂接受。
- evaluator 从 ccg 给 llm_writeback 计分（n_accepted、confound_wrongly_accepted 正确）。

## 重要说明：旧数据不受影响
本修复只影响**第二轮重跑**的数据。第一轮已上传的 F3m 等数据是修复前产生的（candidates 里 status 已固化为 accepted），重新跑 evaluate 仍会显示旧值——这是正常的，第二轮重跑后才会体现修复。
