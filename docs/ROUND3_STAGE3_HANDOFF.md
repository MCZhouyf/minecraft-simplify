# 第三轮 阶段3 交接：真机跑批前的注入链路修复 + K7 可观测性 + 代价分层落数

## 本阶段完成（容器三次查验，全新克隆应用后离线 136 passed / 29 deselected）
阶段2 把配置一次配好（离线 118 passed），但前两轮真机跑批之所以出问题，根因在
runner/sanity/solvability 这条"跑批—验证—回填"链路上有几处与阶段2 新套件不同步的硬编码与
潜在崩溃。本阶段逐一修掉，并把论文 4.4 的模拟器代价分层从"代码里实现了"推进到"跑批数据里
能被审计、能产出表4/表6b"。本阶段不动 minecraft-simplify（JS 环境）。

## 应用方式（同阶段2）
解压覆盖到仓库根目录（已是改好的完整文件，无需打补丁）。
应用后必须先：`python3 -m pytest tests -m "not integration" -q` → **136 passed, 29 deselected**，
再跑真机。

## 本阶段改动文件（7 改 + 4 新 = 11 个）
改动：
- `experiments/runner.py`        跑批入口：注入通道、None 步数、配置透传、过期偏差跳过、--seeds/--no-necessity/--no-neighbor
- `experiments/sanity_check.py`  方案B注入有效性检查：GT 真值改从 yaml 派生 + 修嵌套 payload 读取 + freedo 可解性确认
- `experiments/tasks.yaml`       次要套件 id 重映射为有效新 id（feedback_ladder / confound，见第 7 项）
- `Adam/tcpg/runtime.py`         K7 代价分层落数(floor 模型) + 新增 cost_model 事件 + compile/intervention_start 补字段 + candidate_records 同步池统计 + 组件消融开关
- `mc_drift/solvability.py`      `--write` 回填支持 block 格式 YAML（原仅支持 inline）
- `mc_drift/biases/biases.yaml`  oracle_plan_steps 已用 solvability 回填真值（不再是 None）
- `mc_drift/fabric-mod/src/main/java/mcdrift/GateConfig.java`  allowFurnaceTick 加 nighttime（C3）

新增：
- `experiments/audit_k7.py`            逐 run K7 审计（可 import 的 `audit_dir()` + CLI）
- `experiments/cost_layering_report.py` 出表4/表6b 的聚合脚本（可 import 的 `build()` + CLI）
- `tests/test_round3_stage3.py`        本阶段 18 条离线回归测试
- `docs/ROUND3_STAGE3_HANDOFF.md`      本交接文档

## 逐项修复说明（前两轮问题的根因）

### 1. [阻断·注入失效] runner 的 DATAPACK_BIASES 硬编码过期 —— 5/8 偏差注入走错通道
原 `DATAPACK_BIASES = {"C1","C2","C3"}` 是旧套件留下的。新套件里走数据包注入的实际是
R2(datapack_recipe)、R5/R6(datapack_tag)，而 C1/C3 是 mod_event。硬编码导致一半偏差的注入
通道判断反了——这正是"discovery 全 natural_success / 注入像没生效"的根因之一。
**改为从 biases.yaml 的 mechanism 字段派生**：mechanism ∈ {datapack_recipe, datapack_tag}
才算数据包注入。与 `mc_drift/datapack_gen.py` 的 `DATAPACK_MECHANISMS` 同源，不再各写一份。

### 2. [阻断·崩溃] oracle_plan_steps=None 触发 `None <= 50` TypeError
阶段2 把 oracle_plan_steps 全置 None（旧步数失效），但 runner 里有 `steps <= cap` 的比较，
None 直接抛 TypeError，跑批一开就崩。**加 None 守卫**：缺失/None 时回退到一个保底大值（100），
并配合下面第 9 项把真值回填进 yaml。

### 3. [阻断·配置丢失] min_verifications_per_cand 没透传到 runtime
tasks.yaml 里 `defaults.min_verifications_per_cand=2`，但 runner 构造 runtime 配置时没带上这个
键，运行时拿的是 CONFIG_DEFAULTS 的 0——等于"最小验证次数"约束从没生效。**补齐透传**，并把
n_min/delta/tau_acc/tau_rej/sim_verify_cost/sim_cost_mode 一并按显式键透传，避免再漏。

### 4. [正确性] sanity_check 的 GT_TARGET 硬编码过期 + 嵌套 payload 读死
旧 sanity 的 GT_TARGET 表把 C1/C3 标成 held_tool（错的，应是 nearby_block / time_of_day），
且 R4/R5/R6/C4 干脆没有；又因 K7 的 payload 是**嵌套**结构（`e["payload"]["..."]`），旧代码按
扁平结构读，读到的全是空。两个 bug 叠加 → sanity 结论不可信。
**GT 真值改从 biases.yaml 的 ground_truth.target 派生**（与 evaluate.py 同源），并 import
`evaluate.is_gt` 做严格谓词匹配（target+property+comparator+value 都对，而非只看 target）；
ctx_snapshot 等读取改回正确的嵌套路径；"提案过的 / 进了池子的"目标改从 summary 的 candidates
列表统计。注入失效仍然是 **BLOCKING**（全 natural_success 直接判失败、停下排查），GT 命中给
三态（已写回 / 进池未决 / 从未提出）。

### 5. [对论文] 代价分层用了"一律 2.0"的平表，破坏 4.4 与表6b
阶段2 实现里 sim_verify_cost 固定 2.0，等于资源输入型和情境约束型的代价没真正拉开档次，表6b
的 α 消融做不出"廉价真因优先"的对比。**改为 floor 模型**：
`cost(h) = max(sim_verify_cost, est_steps)`（资源输入型 sim_verifiable=True 才适用 floor，
情境约束型恒为 est_steps）。可经 cfg 的 `sim_cost_mode` 切换 `"floor"`(默认) / `"flat"`(回旧行为，
向后兼容、亦作对照档)。这对应论文 4.4 "代价按候选变量类型分层"，floor 保证模拟器验证有一个
下限代价、又不会把廉价真因抬到和昂贵候选一样高。

### 6. [可观测性] K7 里没有 cost / sim_verifiable —— 审计无从查
手册 §2 步骤4 要"查 K7 的 cost 字段验证代价分层"，但 K7 根本没记。**补**：compile 事件加
sim_verifiable；intervention_start 加 cost 和 sim_verifiable；新增 **cost_model 事件**记录
{action, alpha, c0, sim_verify_cost, sim_cost_mode, trigger_budget, costs:{cid:{cost,est_steps,
sim_verifiable}}}。这样 audit_k7.py 能交叉核验"intervention_start 里的 cost == cost_model 里
同 cid 的 cost"，cost_layering_report.py 能按 α 分组出表6b。

### 7. [鲁棒性+卫生] 过期的 C2/E1/P1 套件引用：已重映射为有效 id + 保留跳过卫语句
tasks.yaml 的 feedback_ladder（旧 C2/P1/E1）和 confound（旧 F1/F2/F3 用到 C2/E1）引用着本阶段
未注册的偏差，原样会静默跳过、产出空表（易误以为跑过了 Table 3/7）。本阶段做两件事：
(a) **runner 加跳过卫语句**（`if bias_id not in TASKS["biases"]: return`，放在 deferred import
之后、用 env 之前）作为兜底，任何一处过期引用都不会再拖垮整批；
(b) **tasks.yaml 把两套件的 id 重映射为有效的新 id**（feedback_ladder→[R1,R4,C1] 均为 mod_event
偏差，数据包偏差不发 mod 反馈不能用于反馈梯度；confound→F1=R6@deep / F2=R1+sticks /
F3,F3m=C1+伪近邻方块），不再静默空跑。
**重要诚实声明**：这两个套件是 **次要交付**（不在手册 §6 的阶段3 出数清单里），其试验场几何（伪
共现条件的具体布置）是从旧套件搬过来的、**未经新偏差验证**——它们能枚举、能跑、不崩，但 Table 3/7
的数据在真机核对几何之前不可直接采信。阶段3 已验证的**主交付**是 discovery 八偏差 + 代价分层 +
α 消融 + C3 Java。

### 8. [C3·Java] allowFurnaceTick 缺 nighttime 分支
按手册 §4 原文加 `case "nighttime" -> (world.getTimeOfDay() % 24000L) >= 12000L;`。
**注意一个边界**：手册的 nighttime 阈值是 12000，而 C3 真因 GT 是 time_of_day∈[13000,23000]，
两者不完全重合（12000–13000、23000–24000 这两段算 night 但不在 GT 窗口内）。本阶段**按手册原文
落**，把这个差异记在这里，真机验 C3 时留意 sanity 是否因这段错配出现边缘误判；若需严格对齐 GT，
把阈值改 13000 并在窗口上界加 `< 23000L` 即可。改完 `cd mc_drift/fabric-mod && ./gradlew build`
重建 jar 覆盖 mods/。

### 9. [本阶段新发现·阻断回填] solvability.py 的 `--write` 对 block 格式 YAML 静默失效
原 `backfill_yaml` 只用正则匹配 inline-flow 写法（`solvability: {... oracle_plan_steps: N}`），
但仓库里 biases.yaml 提交的是 **block 格式**（`solvability:` 换行后缩进 `oracle_plan_steps: N`）。
于是 `--write` 跑完"看起来成功"、实际一个字没改 → oracle 步数永远回填不进去（这条和第2项叠加，
就是前两轮 oracle 相关老是对不上的原因）。**改为两种格式都认**。Codex 跑批协议里有
`solvability.py --all --write` 这一步，所以这个必须能真改。

本阶段已用修好的脚本回填好真值（容器实算，已 parse 通过、runner 能加载）：
R1=42, R2=22, R4=131, R5=38, R6=331, C1=163, C3=78, C4=184；
循环依赖偏差（钻石矿需钻石镐）被验证器正确拒绝（reject_reason: recipe_unreachable）。

## 三个新脚本怎么用
- `python3 experiments/sanity_check.py`  —— 每批跑完先验注入有效性（方案B，无探针）。
- `python3 experiments/audit_k7.py --runs experiments/runs`  —— 逐 run 查 trigger_abort/
  ctx_resync/neighbor_expand/writeback 计数，并校验代价分层（cost 字段与 cost_model 对齐，
  容差 0.011）；发现代价违例 exit 1。旧 run 缺 cost_model 时优雅报 None、不崩。
- `python3 experiments/cost_layering_report.py --runs experiments/runs --out report.json`
  —— 出表4（按资源输入/情境约束两类的 precision/recall/f1/错误写回率/平均 n_eff）与
  表6b（按 α 分组的 真因首次验证步数 / gt_accepted / 验证步数）。

三个脚本都既能 CLI 跑、也能 `import` 后拿结构化结果（`audit_dir()` / `build()`），方便接你后续的
出图脚本。

## 仍然推迟（不阻塞本阶段 8 偏差）
- R3 craftReinforcedHandle、C2 craftBoat：技能/动作未注册，按手册 §5 待开发。
- 混杂(Table 7)、反馈阶梯(Table 3)：id 已重映射为有效新 id（见上第 7 项）、能跑不崩，但试验场
  几何未按新偏差验证，数据推迟到真机核对几何后再采信。合取(J1/J2)：偏差未注册，推迟。

## 下一步（真机跑批，见随附 Codex 提示词）
1. 应用 zip → 离线 136 passed。
2. **先备份再清空 `experiments/runs/`**：仓库提交了 21 个阶段2 示例 run（含已退役偏差 C2/E2/P1/R3），
   不清掉会污染阶段3 的 evaluate/audit/cost_layering 出数。`mv experiments/runs
   experiments/runs_stage2_samples_backup && mkdir experiments/runs`。
2. **先备份再清空 `experiments/runs/`**：仓库提交了 21 个阶段2 示例 run（含已退役偏差 C2/E2/P1/R3），
   不清掉会污染阶段3 的 evaluate/audit/cost_layering 出数。`mv experiments/runs
   experiments/runs_stage2_samples_backup && mkdir experiments/runs`。
3. `solvability.py --all --write` 回填确认（现在能真写）。
4. 构建 C3 的 Fabric jar（`cd mc_drift/fabric-mod && ./gradlew build`）并覆盖到 mods/。
5. 按 R2→R5→R6→R1→R4→C4→C1→C3 逐个：sanity 验注入 → freedo_oracle 测 oracle 步数回填 →
   三模式(tcpg/freedo_oracle/llm_writeback) ≥3 seed（`--all --seeds 0,1,2`） → audit_k7 →
   evaluate + cost_layering_report。
6. 表6b：C4 跑 `--cost-alpha {0,0.5,1} --min-floor 0` 三档。
