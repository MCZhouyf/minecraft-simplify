# 第 3 阶段交付与执行指引（谓词求值层 + 签名引导）

## 本次交付的文件
| 文件 | 状态 | 说明 |
|---|---|---|
| env/mineflayer/lib/predicates.js | 覆盖 | 12 条原语求值器 + stateSnapshot（每原语一个纯函数 + 注册表；K3 语义：known=false ≠ 0） |
| env/mineflayer/index.js | 覆盖 | 仅 3 处插入：第 8 行 require + 文末两个路由 /eval_predicates、/state_snapshot（/start /step 未动；基于 commit 74ca0a1 打补丁，node --check 已通过） |
| Adam/tcpg/schema.json | 覆盖 | 真实核心签名 Σ_MC：12 条原语声明 + 维度—槽位白名单 + observe_only（weather） |
| Adam/tcpg/predicates.py | 覆盖 | K3 客户端：validate_predicate / eval_predicates / state_snapshot / in_whitelist |
| Adam/tcpg/signature_bootstrap.py | 新增 | dump（活体）→ derive（离线）→ report（离线），产出论文 4.3/B.4 的两个数字 |
| tests/fixtures/state_snapshot_example.json | 新增 | 引导管线的离线夹具 |
| tests/test_predicates_offline.py | 覆盖式新增 | 9 个离线用例（含 K1×Σ 一致性交叉检查——每条偏差真值必须在其维度白名单内且比较子合法） |
| tests/integration/test_predicates.py | 新增 | 4 个活体用例（已知态全原语 / unknown 语义 / sky 双侧 / 快照字段） |

离线全量在交付机已验证：**21 passed**（12 旧 + 9 新）。

## 执行顺序（半天）
1. `pytest tests -m "not integration"` → 21 passed。
2. 重启 Mineflayer 服务无需手动——conftest 的 env 夹具每次起新进程会加载新 index.js。
3. 开世界开 LAN → `export IAP_MC_PORT=<端口>` → 调整 test_predicates.py 顶部两个坐标。
4. `pytest tests/integration/test_predicates.py -x`（四个用例约 3–4 分钟）。
5. 签名引导（产出论文数据）：
   ```bash
   python3 -m Adam.tcpg.signature_bootstrap --dump      # 需要 LAN 在线
   python3 -m Adam.tcpg.signature_bootstrap --derive --report
   cat docs/manual_residue.md
   ```
   预期：12 条核心原语中 **8 条自动导出**（y_level/time_of_day/held_tool/held_item/inventory_count/block_below/sky_exposed/weather），4 条人工（nearby_block/station_type/station_base_block/ingredient_type——需要平面状态之外的世界查询或动作上下文），人工残留行数以报告为准。**把这三组数字回填论文 v4.2 的 4.3 节与 B.4 表（"___/11" 处注意：核心原语数现为 12，论文 B.2 表需同步加 station_base_block 一行）。**
6. 全绿提交：`git add -A && git commit -m "stage3: predicate evaluation layer (K3) + signature bootstrap" && git tag v0.3`。

## 设计要点
- **known=false 语义**贯穿全层：空手查 type、无工作站查基座、区块未加载、ingredient_type（需动作上下文，第 5/6 阶段由编译器在合成时求值）→ 全部 known=false，调用方禁止当 0 用（unknown 观测不入双池）。
- nearby_block 的"查不到"两分支：半径 ≤16（bot 自身区块必加载）→ 缺席可信，known=true 值 0；半径 >16 → known=false。
- held_item.type 支持类目匹配（shovel/pickaxe/axe/sword/hoe → endsWith），与 X2 真值 value: shovel 对齐；held_tool 空手 → tier=-1，known=true（"没有工具"是确定事实）。
- station_base_block 的 value="stone" 匹配石质家族集合，与模组 STONE_BASES 一致。
- /eval_predicates 同一请求在同一事件循环 tick 内同步求值（快照一致性）。

## 已知坑
1. index.js 若与你本地有 diff 冲突：本补丁只有 3 处插入（require 一行 + 文末两个路由块），手动套用即可，块内容见文件尾部。
2. test_core_primitives 的 s1（station_base_block）要求 setblock 的石头先于熔炉放置——用例里顺序已正确，自己改用例时别颠倒。
3. sky_exposed 在 DEEP_SPOT 若返回 known=false：头顶柱有未加载段，把 DEEP_SPOT 挪到 bot 出生区块附近。
4. --dump 会自起一个 VoyagerEnv（占用 3000 端口），别与正在跑的集成测试并行。
