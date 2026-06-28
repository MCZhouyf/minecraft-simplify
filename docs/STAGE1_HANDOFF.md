# 第 1 阶段交付与执行指引（datapack 偏差注入器）

## 本次交付的文件（直接覆盖到仓库对应路径）
| 文件 | 状态 | 说明 |
|---|---|---|
| mc_drift/config.yaml | 覆盖 | 真实 K0 字段；**必须先填 minecraft_dir / world_name / minecraft_version** |
| mc_drift/biases/biases.yaml | 覆盖 | R1–R3、C1–C3 共 6 条全量真值配置（已通过 schema 与动作词表校验） |
| mc_drift/schemas/k1_bias.schema.json | 覆盖 | K1 完整 JSON Schema（含 mechanism 条件分支） |
| mc_drift/datapack_gen.py | 覆盖 | 完整实现：load/generate/install/uninstall/export_mod_config + CLI |
| env/bridge.py | 修改 | 仅新增 reload_datapacks()（step() 之后），/start /step 协议未动 |
| tests/conftest.py | 覆盖 | integration 标记门控（IAP_MC_PORT）+ env 夹具 + 4 个测试助手 |
| tests/test_datapack_gen.py | 覆盖 | 11 个离线单测（本机已全绿，0.4s） |
| tests/integration/test_bias_datapack.py | 新增 | 7 个世界内验收（每偏差 neg+pos，finally 自动还原） |

## 执行顺序（人/Codex 按此走，预计半天）
1. `pytest tests -m "not integration"` —— 应 11 passed（验证交付完整落盘）。
2. 填写 `mc_drift/config.yaml` 三个 CHANGE_ME 字段。
3. `python -m mc_drift.datapack_gen --biases all --install` —— 生成并拷入世界存档 datapacks/。
4. 启动 Minecraft → 打开该世界 → **开 LAN 且允许作弊** → 记下端口。
5. `export IAP_MC_PORT=<端口>`（如 Mineflayer 服务端口非 3000，另设 IAP_MF_PORT）。
6. 调整 `tests/integration/test_bias_datapack.py` 顶部 `ORE_SPOT` 为你试验区内一个已加载坐标。
7. 逐偏差验收：`pytest tests/integration -k R1 -x` → R2 → R3 → C1 → C2 → C3。
8. 全量 + 还原：`pytest tests/integration -m integration`（含 test_restore_vanilla_furnace）。
9. 全绿后：`git add -A && git commit -m "stage1: datapack bias injector (R1-R3,C1-C3)" && git tag v0.1`。

## 设计要点备忘（验收时核对）
- 失败语义统一为"动作执行但库存无产出"；neg 用例**只断言库存**，collectBlock 超时是预期失败路径。
- pack_format：1.19–1.19.3→10，1.19.4→12；`reload_datapacks()` 内部用 `/datapack list enabled` 确认，静默不加载会被当场抓住。
- R3 断言读原始物品名 `oak_fence`，绕过 module_utils.rename_item 的木种归一化。
- 测试中的 `/setblock`、`/give` 等指令仅允许出现在 tests/ 与 trial 初始化（CLAUDE.md 纪律）。
- `export_mod_config()` 已就位但当前输出空 gates —— 第 2 阶段在 biases.yaml 追加 6 条 mod_event 条目后即自动生效。

## 已知坑（排查顺序）
1. 集成测试全 skip → 未设 IAP_MC_PORT。
2. reload_datapacks 返回 False → 先在游戏内手敲 `/datapack list`：没有 mc_drift 则检查 config.yaml 的 world_name 是否就是开 LAN 的那个存档；有但 disabled 则 `/datapack enable "file/mc_drift"`。
3. C 类用例 neg/pos 都失败 → ORE_SPOT 区块未加载或坐标在实体方块内，换一个 bot 附近的露天坐标。
4. /give 失败 → LAN 未开作弊，重开 LAN 勾选 Allow Cheats。
