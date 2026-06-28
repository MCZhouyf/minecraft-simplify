# 第 4 阶段交付与执行指引（TCPG 提案器 + 校验器 + 原语准入）

## 本次交付的文件
| 文件 | 状态 | 说明 |
|---|---|---|
| Adam/tcpg/eventlog.py | 新增 | 最小 K7 记录器：$IAP_K7_LOG 设置时追加 JSONL，否则零开销 no-op（第 7 阶段 runner 接管完整 K7） |
| prompts/tcpg_prompt.txt | 新增 | 论文 H.1 提案模板（{whitelist} 由 schema.json 实时渲染 + 各原语值约定） |
| prompts/admission_prompt.txt | 新增 | 原语准入模板（Q 语言文法内嵌 + 观测字段表注入） |
| Adam/tcpg/proposer.py | 覆盖 | K4 全实现：Candidate（cid=sha1 前 12 位，跨回合聚合）、validate（结构 + 白名单 + 已准入透传）、propose_from_failure（≤2 次带错误反馈重试、围栏剥除、按 cid 去重、LLM 依赖注入）、candidates_from_success（无 LLM） |
| Adam/tcpg/admission.py | 新增 | 论文 B.6 全实现：Q 语言分词器+递归下降解析器→元组 AST、static_check（字段白名单+类型+布尔根）、evaluate（unknown 传播 + and/or 短路 + inventory[item] 缺席=0）、dynamic_check（确定性/非常量/known≥80%）、record_rejection 触发计数、try_admit 全流水线（k_trigger=3、每回合≤1、全局≤8、快照≥50）→ K9 写 schema_runtime.json、FIELD_FAMILY 宏族匹配（可干预升级判据，第 5 阶段编译器接管权威版）、eval_admitted（经 /state_snapshot 在 Python 侧求值） |
| tests/fixtures/failure_events/*.json | 新增 | 10 个失败事件夹具（R1-R3/C1-C3/P1/X1/X2/E1，各含观测+真值标注） |
| tests/test_proposer.py | 新增 | 7 用例：cid 稳定性、白名单校验、围栏解析、重试合并、垃圾容错+去重、放弃路径、成功分支过滤 |
| tests/test_admission.py | 新增 | 25 用例：Q 合法 8 例/非法语法 6 例/静态拒绝 6 例、求值语义（unknown+短路）、动态检查三过滤、全流水线（触发→准入→K9→validate 透传→去重）、三道限速、坏回复拒绝 |
| experiments/eval_proposer.py | 新增 | 表 5 指标闸门：--mock 无钥冒烟（已过：recall 1.0 / compliance 1.0）；真实模式阈值 合规>0.85 且 召回>0.70，--enforce 不达标退出码 1 |

离线全量在交付机已验证：**53 passed**（21 旧 + 7 proposer + 25 admission）。

## 执行顺序
1. `pytest tests -m "not integration" -q` → 53 passed。
2. `python3 experiments/eval_proposer.py --mock --enforce` → 退出码 0。
3. **真实 LLM 闸门**（需要 API 钥）：
   ```bash
   export OPENAI_API_KEY=...; export IAP_LLM_MODEL=gpt-4o   # 或你的代理 + OPENAI_BASE_URL
   python3 experiments/eval_proposer.py --enforce
   ```
   不达标先迭代 prompts/tcpg_prompt.txt（这是唯一允许动的调优面），**禁止**为提高合规率放宽 validate。把最终三组数字（first-pass / compliance / per-dimension recall）记入论文表 5 草稿。
4. 提交：`git add -A && git commit -m "stage4: TCPG proposer + validator + primitive admission (K4/K9)" && git tag v0.4`。

## 设计要点
- **准入默认关闭**：没有任何代码自动调 try_admit；第 7 阶段 runner 仅在签名恢复实验任务（admission_enabled: true）才接线。schema_runtime.json 与 admission_rejects.json 已属运行时产物，建议加入 .gitignore。
- **白名单外拒绝即触发计数**：proposer 校验失败时自动 record_rejection（软依赖，admission 不在也不报错）——这是 B.6 触发通路的埋点。
- Candidate 的 **cid 不含 dimension/source/pools**——同一谓词从失败分支与成功分支提出时聚合到同一双池（第 6 阶段消费）。
- eval_admitted 返回与 K3 完全同构的结果字典，第 6 阶段闭环按 origin 路由到 /eval_predicates 或本函数，调用方无感。

## 已知坑
1. 真实 LLM 模式 first_pass_rate 为 null 属预期（仅 mock 包装器记录首轮原始条目；真实模式首轮统计由 K7 validate 事件聚合，第 7 阶段 evaluate 实现）。
2. Q 语言禁 `**`、函数定义、动作引用——admission_prompt 已声明，若真实 LLM 仍产出，static_check 会拦，无需手工兜底。
3. try_admit 的 snapshots 参数由调用方提供（第 7 阶段 runner 每 trial 录制 experiments/logs/state_snapshots/）；当前阶段测试用合成快照。
