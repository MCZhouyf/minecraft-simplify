# 第 6 阶段交付与执行指引（双池后验 + 验证闭环 + 免 LLM 图规划 —— 全项目核心）

## 本次交付的文件
| 文件 | 状态 | 说明 |
|---|---|---|
| Adam/tcpg/posterior.py | 覆盖 | DualPool（Beta(1,1) 双池、同批 MC 估 q̂/Γ̂⁺、invalidate_last 作废契约、decide 三态）+ Acquisition（q̂Γ̂⁺ 贪心 + 每 rr_every 次轮询保底——定理 1 鸽笼保证的工程对应）+ scarce_side |
| Adam/tcpg/ccg.py | 覆盖 | 条件因果图：E_out/E_in 接口初始化、E_ca 唯一写入口 write_back、assumed_preconds（对象前件 + 采集动作的原版工具档位）、gate_text 提示注入、**plan_from_graph 反向链接零 LLM 规划**（inventory/held_tool 门控折入需求图，催化剂保留语义；其余门控类型→None 回退 LLM）、prune（仅剪 known-false）、to/from_dict 持久化 |
| Adam/tcpg/runtime.py | 新增 | 闭环 Algorithm 1：on_action 单入口 → 候选生成（成功→必要性/失败→恢复）→ 编译注册（INFEASIBLE→observe_only）→ 自然观测路由（known 才入池）→ 验证循环（快照 Ct→稀缺侧计划→重试→undo→Ct 复核→漂移作废→阈值判定→写回）。五档 verification_mode 全实现；freedo_oracle 经 env.reset 实现净库存效果、干预步数计 0；execute_action/llm 依赖注入 |
| Adam/ADAM.py | 修改 | **全项目唯一动它的一次，6 处编辑共 ~70 行**（精确插入块见下） |
| tests/test_posterior.py | 新增 | 5 用例，**直接对照论文 D.1 实算表**：完美分离 δ=0.3 下 q(3)=0.888 / q(4)=0.947 / q(5)=0.975（±0.02）；n_min 卡控、作废恢复、贪心+保底 |
| tests/test_ccg_plan.py | 新增 | 7 用例：闭合链零 LLM、库存抵扣、煤门控折叠（催化剂不重复采集）、工具门控折叠、不可折门控回退、prune 仅剪 known-false、序列化往返 |
| tests/test_runtime_loop.py | 新增 | 6 用例：MockWorld 隐藏 C2 机制全闭环——**真门控 accepted、自然为真的混杂 rejected、写回即改变规划**；off 惰性、llm_writeback 写入混杂（消融下限）、freedo 零步数、预算含 undo 严守 |
| tests/integration/test_regression_off.py | 新增 | 4 用例：off/adam_original 钩子全惰性、tcpg 档图规划接通（craftPlanks）、基线动作原版行为 |
| tests/integration/test_e2e_single_bias.py | 新增 | **头号验收**：C2 实包 + 实干预（装备切换/箱暂存）+ 实谓词的闭环发现→写回→重规划；脚本化 LLM（真门控+自然为真混杂）；K7 断言 writeback/intervention_start；freedo 变体断言干预步数=0 |
| tests/integration/test_undo.py | 新增 | 3 用例：装备 undo 复原 Ct（漂移可检出）、箱存取物品级复原 |

离线全量在交付机已验证：**87 passed**（69 旧 + 18 新）。

## ADAM.py 的 6 处编辑（冲突时手动套用；其余 ~930 行未动一字）
1. 构造函数参数表尾加 `verification_mode: str = "off",`；body 在 `self.track_player = track_player` 后加三行状态（verification_mode / _tcpg_rt / _tcpg_graph_queue）。
2. controller() 规划入口：未达目标分支改为先 `action = self._tcpg_next_graph_action(latest_payload['inventory'])`，None 才走原 planner→actor（图计划命中时跳过两次 LLM 调用）。
3. controller() 动作后：`self.update_memory(...)` 之后插一行 `self._tcpg_on_action(action, added_items, end_item)`。
4./5. planner() 与 actor() 的 `"{causal graph}"` 替换值追加 `+ self._tcpg_gate_text()`（已验证门控以硬约束文本进提示）。
6. 类尾新增 5 个瘦方法：_tcpg_runtime（懒工厂）/_tcpg_gate_text/_tcpg_next_graph_action（K7 plan{source:graph|llm}）/_tcpg_execute_action（重试通道：标准执行+库存进度判据、无 recorder 副作用）/_tcpg_on_action（单路由，异常永不杀死智能体）。
sample_action_once / causal_learning 原始路径一字未动（adam_original 档专用）。

## 执行顺序
1. `pytest tests -m "not integration" -q` → 87 passed。
2. 开世界开 LAN（C 类 datapack 已按 stage1 流程预装）→ export IAP_MC_PORT。
3. 调整三个集成文件顶部坐标（ORE_SPOT/SURFACE_SPOT）。
4. 验收顺序：`pytest tests/integration/test_regression_off.py -x`（最快确认 ADAM 补丁无破坏）→ `test_undo.py -x` → **`test_e2e_single_bias.py -k tcpg -x`（头号验收，约 8–15 分钟）** → `-k freedo`。
5. 全绿提交：`git add -A && git commit -m "stage6: dual-pool posterior + verification loop + LLM-free graph planning (Algorithm 1)" && git tag v0.6`。

## 设计要点
- **预算核算含 undo**：可负担性预检 = 已用 + 计划长 + 重试 1 + undo 长（开发中实测抓到差一步越界并钉成测试）。
- **Ct 复核口径**：held + y（容差 3）；库存复原由 undo 计划自身负责并经 K7 审计——I⁺ 合法新增物品（如合成出的工具留在背包）不算漂移。
- **作废契约贯通**：undo 失败或 Ct 漂移 → invalidate_last，本次观测出池，候选保持未决——池永不被污染。
- e2e 测试的 LLM 是脚本化注入（不依赖 API 钥）；真实 LLM 的提案质量已由 stage4 闸门单独把关，两层关注点分离。
- 重放矿石的 /setblock 属测试夹具特权（重试需要矿存在），智能体自身永不发指令。
