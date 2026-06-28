# 第 1 阶段修复版交付（针对 2026-06-11 集成报错）

## 两个根因（比表面诊断更深一层）
1. **/reload 不稳定（zip file closed）**：对已打开世界的 datapacks/ 目录做文件增删后再 /reload，是原版已知的不稳定路径，失败后服务端保留旧数据（日志 "Loaded 7 recipes" 即重载半途崩溃的产物）。→ 修法：**世界关闭时一次性装好全部包**，会话内只用 `/datapack enable|disable "file/mc_drift_<ID>"` 切换（该命令内部自带安全重载，不触碰文件系统）。
2. **R1–R3 的配方覆写从设计上不可解（INV-1 违例，比测试问题严重）**：mineflayer 的 `bot.recipesFor/bot.craft` 用的是 minecraft-data 内置的**静态原版配方**，不读服务端 datapack。负例侥幸能挡住（服务端结果槽为空），但**正例永远做不出来**——bot 永远按原版形状摆格子。智能体发现条件后也无法成功 = 可解性不变式被破坏。→ 修法：R1–R3 改为**模组 craft_result 门控 + inventory_min 条件**（"合成熔炉须持有 1 煤"，催化剂语义、不消耗），原版配方形状不变 → bot 正常合成 → 正例可解；真值谓词 inventory_count(coal)≥1 不变（R3 改为 inventory_count(birch_planks)≥4）。
   另两处小修：env 夹具在任何 step 前先做一次空 reset（修 "Environment has not been reset yet"）；废弃靠解析聊天回包的 reload_datapacks（onChat 不捕服务端命令回包），验证改为功能性断言。

## 本次交付文件（覆盖到仓库对应路径；会丢弃你工作区的临时修改，请先 git stash）
| 文件 | 变化 |
|---|---|
| mc_drift/biases/biases.yaml | R1/R2/R3 → mod_event（craft_result + inventory_min），C1–C3 不变；机制分布 = 3 datapack_tag + 9 mod_event |
| mc_drift/datapack_gen.py | generate() 改为**每偏差一个包** mc_drift_C1/C2/C3；install/uninstall 要求世界关闭时执行；CLI 输出提示切换命令 |
| env/bridge.py | 删 reload_datapacks，新增 datapack_set(bias_id, enabled) 与 datapacks_enable_only(ids)（经 /step 发 /datapack 命令，不解析回包） |
| tests/conftest.py | env 夹具构造后立即空 reset 一次 |
| tests/integration/test_bias_datapack.py | 只剩 C1–C3 + 原版还原用例；夹具按"仅启用被测包"切换 |
| tests/integration/test_bias_mod.py | 新增 R1/R2/R3 三个用例（含"煤不被消耗"断言）；现共 10 用例 |
| tests/test_datapack_gen.py | 口径更新（12 passed 已在交付机验证） |
| mc_drift/fabric-mod/.../GateConfig.java | craft_result 新增 require=inventory_min 分支（player.getInventory().count） |

## 执行顺序
1. `git stash`（保留你的 config.yaml 本地值，其余临时修改丢弃）→ 解压覆盖 → 把 config.yaml 三个字段填回。
2. `pytest tests -m "not integration" -q` → 12 passed。
3. **重新编译模组**（GateConfig 变了）：`cd mc_drift/fabric-mod && ./gradlew build`，把新 jar 拷入 mods/（覆盖旧的 fabric-mod-0.2.0.jar）。
4. **关闭世界/游戏** → `python -m mc_drift.datapack_gen --biases all --install`（只会装 C1–C3 三个包）→ `--export-mod-config`（现在导出 9 个门控）。
5. 启动游戏 → 打开 New World0609 → 开 LAN（允许作弊）→ export IAP_MC_PORT。
6. 验收顺序：先 `pytest tests/integration/test_bias_mod.py -k R1 -x`（最快确认模组链路）→ R2/R3 → `pytest tests/integration/test_bias_datapack.py -k C2 -x`（确认 datapack 切换链路）→ 其余 → 两个全量。
7. 全绿：`git add -A && git commit -m "stage1-fix: per-pack toggling + R1-R3 as inventory_min gates (INV-1)" && git tag v0.1.1`。

## 论文侧同步（重要，防止文实不符）
biases.yaml 是真值唯一来源，以下随之更新：L1 资源偏差的注入机制列从"数据包·配方"改为"引擎事件门控"；R3 真值谓词改为 inventory_count(birch_planks)≥4；MC-Drift 章节"注入机制为数据包与引擎门控"的表述不变（仍是双通道，只是 L1 挪到门控侧）。我会在论文 v4.2 的下一轮修订中同步第 5 章偏差表。
