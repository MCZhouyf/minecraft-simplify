# Codex prompt — wire the full Figure-2 IaP loop into IAP-Agent

Add this `iap_downstream/` package to the repo (e.g. `experiments/iap_downstream/`).
It implements the **whole Figure-2 closed loop** (propose → verify → intervene →
NOTA → write-back → replan → complete) plus the Stage-B attribution harness,
stdlib-only, with 26 passing tests. Your job is to connect it to the real
simulator, LLM and CCG — **do not** reimplement the loop/posterior/NOTA/planner.

Paste to Codex:

```
任务：把已通过 26 个单测的 iap_downstream/ 接到真实 MC-Drift 仿真器、真实 LLM 提案与写回后
的 CCG，跑通论文图2的完整闭环（在线发现→写回→重规划→完成），并产出下游成功率表（表2/表3
写回后成功率列/表4成功率列）。这是适配任务：只写适配层与加载器，不改
planner/executor/posterior/nota/calibration/agent 的核心逻辑。

仓库 IAP-Agent @ round3-suite-v2，真机端口 37775。包放在 experiments/iap_downstream/。
先：cd experiments/iap_downstream && python3 tests/run_all.py 必须 26/26 全绿，再接线。

== 1. Env 适配器（继承 iap_downstream.env_adapter.Env）==
Stage-B 五个方法：
  reset(task,condition,seed)  空背包+drift condition('origin'|'drift')+seed 重置；返回 snapshot()
  step(ground_action)->StepResult(ok)  ok=True 当且仅当动作的【世界前置】成立且仿真器确实执行
                                       （drift 下世界强制注入门控）。用世界模型，不是 planner 的 belief。
  holds(pred) / snapshot()(只暴露可观测、不泄漏隐藏门控) / goal_of(task)
图2发现半区两个钩子（关键）：
  probe(assignments, action_name)->bool
     把 assignments(干预，如 {"y_level":-8} 或 {"water_radius":0})临时施加到当前世界，
     检验 action_name 的【真实世界前置】是否成立，返回 bool，然后恢复世界（纯可行性试验，
     不持久改变任务状态；每次记 1 次干预）。这就是双侧对照/边界干预取证的接口。
  signatures()->[signature,...]
     世界可枚举的门控签名模板（深度/邻近/档位/计数...），每个含：
     {"target","property","kind":"num"|"bool","var","comparator",
      "true_set":{var:使谓词为真的赋值}, "false_set":{...为假...},
      "probe_values":[边界搜索候选值], "achiever":能在规划里满足该门控的动作名, "cost":..}

== 2. Proposer（继承 iap_downstream.proposer.Proposer）==
propose(action, observable)->[Candidate]：用真实 LLM 为失败动作产出 typed 候选门控
（先验偏资源类，符合论文“LLM 初提资源候选被否”的设定）。Candidate 字段见 proposer.py：
action,label,kind,var,comparator,true_set,false_set,probe_values,achiever,cost,source。

== 3. CCG <-> CausalGraph 加载器（真实 ccg.json）==
ccg.json: e_out{action->产物item}, e_in{action->[输入item]}, e_ca{action->[cid]},
          conditions{cid->{target,property,comparator,value,status,...}}, rejected。
每个 action -> iap_downstream.Action：
  add = [Atom("have",(e_out[action],))]
  pre = [Atom("have",(it,)) for it in e_in[action]]
      + [gate_pred(conditions[cid]) for cid in e_ca.get(action,[]) if status=="accepted"]
  gate_pred: var=f'{target}.{property}'; 数值->Threshold(var,comparator,float(value));
             序数(tier wood<stone<iron<diamond)->Threshold(var,comparator,ORDINAL[value])。
为每个门控变量注册“达成动作”schema（带 set-effect），并在 Env 适配器里映射到真实 skill：
  descend(target) sets y_level; equip_tool(target) sets held_tool.tier; approach(...) sets 邻近半径。
写回用 CausalGraph.add_gate(action, gate)（agent 已自动调用，无需手写）。

== 4. 跑两条 ==
(A) 完整闭环（图2）：用 stale 图（before：去掉漂移注入门控的 CCG）作起点，跑
    iap_downstream.agent.run_iap_episode(env, task, G_stale, proposer, posterior_cfg, cost_alpha, seed)。
    每个情境任务 N>=5 种子，drift 下应 100% 完成、discovered 里出现该门控（mineDiamondOre
    用操作值，与正文 Route B 一致）；origin 下 0 干预完成。记录 completed/interventions/replans/discovered。
(B) 冻结图归因（Stage B，填表）：用 before/after/oracle/各消融的写回后 CCG 跑
    python3 -m iap_downstream.run_downstream --real --seeds 5 \
        --out experiments/results/table_downstream.csv
    过 harness.sanity_checks（origin after≈before；drift before≈0；drift after>before），
    再用 metrics.to_table2/to_table3_success/to_table4_success 合并进 evaluate.py：
    表2=各方法 origin/drift 成功率(+CI)；表3=after drift 成功率列；表4=各消融 drift 成功率列。
    顺带把 minus_dual_pool/minus_costaware 写回后的 CCG 喂进 (B)，给这两个发现侧为 null 的组件
    一个下游成功率数字。

== 5. 闸门 / 预算 / 冒烟 ==
- sanity_checks 必须为空才报成功率；drift 的 before 不接近 0 说明门控没真生效或 non-gated 部分
  本就可解，先修任务/环境。
- Stage-A 干预成本与下游执行步数分开记账（agent 已分开返回 interventions vs steps）。
- 冒烟：先 craftBoat(C1)+mineDiamondOre(C3) 各 2 种子跑 (A)，确认在线发现该门控且完成；再全量。
回报：run_iap 的完成率/发现门控、table_downstream.csv、sanity-check 结果、合并进表2/3/4 的片段，
以及任何未达不变量的诚实说明（不要硬凑）。
```

## Where the only real risk is

Three adapter points; everything else is proven by the mock + 26 tests:
1. `step.ok` and `probe` must reflect the **true world** preconditions (incl. the
   injected gate), not the planner's belief — otherwise `before` won't fail in
   drift and `probe` won't separate a real gate from a spurious candidate.
2. `snapshot` must not leak the hidden gate as a belief.
3. `signatures` `true_set`/`false_set` must actually flip the gate in the world,
   and `achiever`/`bindings` must map to the real "descend / approach / equip"
   skills so write-back is plannable downstream.
