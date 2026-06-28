# 第 2 阶段交付与执行指引（Fabric 模组 mcdrift：引擎事件门控 P1/P2/X1/X2/E1/E2）

## 本次交付的文件
| 文件 | 状态 | 说明 |
|---|---|---|
| mc_drift/fabric-mod/build.gradle, gradle.properties, settings.gradle | 新增 | 按你们环境锁定：MC 1.19 / yarn 1.19+build.4 / loader 0.14.18 / Fabric API 0.58.0+1.19 / Java 17 |
| mc_drift/fabric-mod/src/main/resources/fabric.mod.json, mcdrift.mixins.json | 新增 | 模组声明与 mixin 注册 |
| .../java/mcdrift/McDrift.java | 新增 | 入口：读 config/mcdrift.json、注册方块破坏门控（Fabric API，无 mixin）、/mcdrift reload 命令、mtime 热重载 |
| .../java/mcdrift/GateConfig.java | 新增 | K2 解析与三类门控求值（block_break / craft_result / furnace_tick），含 INV-2 三档反馈 |
| .../java/mcdrift/AuditWriter.java | 新增 | K8 审计日志 logs/mcdrift_audit.jsonl（熔炉事件按 100 tick 节流） |
| .../java/mcdrift/mixin/SlotCanTakeMixin.java | 新增 | 合成门控：CraftingResultSlot 拒绝交付产物（同时覆盖工作台与背包 2×2 网格） |
| .../java/mcdrift/mixin/AbstractFurnaceBlockEntityMixin.java | 新增 | 熔炼门控：取消 tick 冻结熔炼进度 |
| mc_drift/biases/biases.yaml | 覆盖 | 12 条全量（追加 P1/P2/X1/X2/E1/E2 的 mod_event 条目，含 ground_truth 与 feedback_text；已通过 schema 与动作词表校验） |
| tests/test_datapack_gen.py | 覆盖 | 12 条偏差口径更新 + 新增 K2 导出断言（本机已全绿：12 passed） |
| tests/integration/test_bias_mod.py | 新增 | 7 个世界内验收（每门控 neg+pos，finally 自动关闭全部门控） |
| docs/STAGE2_HANDOFF.md | 新增 | 本文件 |

## 执行顺序（预计 1–1.5 天，多数时间在编译与逐条验收）
1. `pytest tests -m "not integration"` —— 应 12 passed。
2. **编译模组**（一次性）：
   ```bash
   cd mc_drift/fabric-mod
   gradle wrapper --gradle-version 8.6     # 仓库未附带 wrapper 二进制；需本机有 gradle 与 JDK 17
   ./gradlew genSources                    # 生成 yarn 反编译源码，备查映射名
   ./gradlew build                         # 产物 build/libs/mcdrift-0.2.0.jar
   ```
3. 把 jar 拷入 `<minecraft_dir>/mods/`，重启游戏一次，日志应出现 `[mcdrift] initialized; enabled gates: []`。
4. 导出门控配置并进世界：`python -m mc_drift.datapack_gen --biases all --export-mod-config`（写到 `<minecraft_dir>/config/mcdrift.json`；注意这一步导出的是全部 6 个门控——**正式验收时由测试夹具按单门控覆写**，不要全开着跑别的实验）。
5. 开世界 → 开 LAN（允许作弊）→ `export IAP_MC_PORT=<端口>`。
6. 调整 `tests/integration/test_bias_mod.py` 顶部三个坐标常量（SURFACE/SHALLOW/DEEP）为你试验区的实际位置。
7. 逐门控验收：`pytest tests/integration/test_bias_mod.py -k P1 -x` → P2 → X1 → X2 → E1 → E2。
8. 全量 + 关闭还原：`pytest tests/integration/test_bias_mod.py -m integration`。
9. 全绿后提交：`git add -A && git commit -m "stage2: fabric mod event gates (P1-P2,X1-X2,E1-E2) + K8 audit" && git tag v0.2`。

## 设计要点备忘
- **失败语义统一为无产出**：block_break 取消破坏（不掉落）；craft_result 经 Slot#canTakeItems 拒绝交付（mineflayer 的 bot.craft 拿不到产物即超时返回，背包 2×2 与工作台同被覆盖）；furnace_tick 取消整个 tick（cookTime 与 burnTime 双冻结）。
- **配置热重载双通道**：mtime 自动重载（≤2s 节流，测试夹具的兜底）+ `/mcdrift reload` 强制重载。两者任一可用即可，自动化不依赖聊天权限。
- **审计**：每次门控判定写一行 K8；熔炉事件按（坐标×偏差）每 100 game tick 节流，否则 20Hz 刷爆日志。
- E2 的 result_match 为 ".*"（按论文设计门控**所有**合成）——验收 E2 时其他合成类测试不要并行。

## 已知坑（按概率排序）
1. **mixin 编译失败（最可能）**：yarn 映射名差异。两个 mixin 的 method 签名已按 yarn 1.19 写并在类注释中标注了排查法——`./gradlew genSources` 后打开生成的 `Slot` / `AbstractFurnaceBlockEntity` 源码核对方法名，改 `method=` 字符串即可。
2. **CommandRegistrationCallback 编译失败**：Fabric API 0.58.0 若无 command-api-v2，按 McDrift.java 顶部注释切到 v1 签名（两行改动）；丢掉命令也不影响验收（mtime 热重载兜底）。
3. E1/P2 的 neg 用例"成功了"：先确认 furnace_tick mixin 生效——夜间手动往熔炉放料观察是否不烧；若烧，看坑 1。
4. E2 的 SHALLOW_SPOT 必须真正无天光（头顶完全封闭）；半透明方块会让 isSkyVisible 为真。
5. X1 在浅层失败后方块仍在原地（破坏被取消是预期），重复跑同一用例前 setblock 会覆盖，无需清理。
6. 模组日志看不到 [mcdrift]：jar 没进对 mods/ 目录，或游戏用的不是 fabric-loader-0.14.18-1.19 这个版本启动。
