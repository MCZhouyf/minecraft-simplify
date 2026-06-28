# 修复包 v2：签名诱导的邻域扩展(去领域定制)+ equip 干预加固

## 为什么从 v1 改到 v2(回应"领域定制/伤泛化"质疑)
v1 把工具档位序 ["wooden","stone","iron","diamond","netherite"] 和 y 层魔数 ±16
硬编码进 proposer——这会被审稿人指为"依赖手填的 Minecraft 知识,与论文的泛化主张矛盾"。
v2 从根上消除该问题:
1. 序结构来自签名,不再硬编码。schema.json 新增一等公民字段 ordered_domains,
   声明哪些 value_type 是"有序枚举"及其序(Σ_MC 中仅 tier_enum)。proposer 读它,
   不含任何环境特定常量。换环境(Σ_ALF)只改签名,proposer 一行不动。
2. 移除数值魔数。inventory_count / y_level 等连续阈值不再做枚举式邻域扩展——
   其阈值偏差由双池后验的 δ-间隔判定吸收。邻域扩展只作用于"差一档全错"的离散有序枚举。
3. golden 明确排除在 tier_enum 之外(非单调采矿档位),作为"序由签名定义而非穷举所有取值"的例证。

## 变更文件
- Adam/tcpg/schema.json：新增 ordered_domains（tier_enum 有序序列）
- Adam/tcpg/proposer.py：_neighbors_for 改为读 schema.ordered_domains + value_type；
  删除 TIER_ORDER 硬编码与 y/count 魔数；expand_neighbors / propose_from_failure(expand=True) 不变
- prompts/tcpg_prompt.txt：漂移声明改为环境无关措辞；不再要求 LLM 穷举阈值（系统自动测相邻值）
- tests/test_proposer.py：邻域用例改为"读签名序"；新增环境无关性证明用例
  (注入 size_enum 新有序域 + 新原语，零 proposer 改动即可扩展)；新增"数值不扩展"用例
- tests/test_runtime_loop.py：C2 闭环用例（v1 已更新，沿用）
- Adam/tcpg/executor.py：equip 确认重试(执行→读 heldItem 确认→至多 3 次→失败抛
  'equip unconfirmed')；run_plan 每原语 retries=1 次瞬时错误重试(作废契约不变)
不动：posterior.py / ccg.py / runtime.py / compiler.py / ADAM.py / mc_drift/。

## 测试
离线全量 **91 passed**(含环境无关性证明用例)；eval_proposer --mock 召回 100%(每偏差 4 提案)。

## 论文措辞(放入方法 4.x 节，见 docs/paper_neighbor_expansion.md)

## 重跑验证
合入后离线 91 passed，再开 LAN 重跑 C2 真实单点：
rm -rf experiments/runs/discovery/C2_tcpg_minimal_s0
OPENAI_API_KEY=... OPENAI_BASE_URL=https://xiaoai.plus/v1 IAP_LLM_MODEL=gpt-4-turbo \
python3 experiments/runner.py --suite discovery --bias C2 --mode tcpg --seed 0
预期 episodes.jsonl 末行 decided 含 held_tool=accepted（且 value=diamond）。
