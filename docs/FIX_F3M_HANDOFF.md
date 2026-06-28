# 修复：F3m 混杂用例空转 + E1 时间错误（独立修复，不属任何阶段）

基于 commit 23dd366（阶段2已合入）。Codex 重跑后发现 F3m 的 llm_writeback proposal_calls=0。

## 诊断（两个连带问题）

### 问题1：F3m 自相矛盾，从不触发失败（Codex 发现，确认正确）
F3m 是 E1（炼铁需白天）的镜像，本意：真因（白天）预满足、失败由别的触发，测方法会不会把配方输入误当新门控。但旧设计 `/time set 6000`（正午）满足了真因白天，**且** E1 基础 inventory 含 raw_iron+coal+furnace（配方输入齐全）——所有炼铁条件都满足，smeltRawIron 每个 episode 必然成功。方法是失败驱动的，无失败→提案/写回链从不启动→proposal_calls=0。这和第一轮 X2（一直持铲→挖沙不失败）同类病：任务设计让注入失效。

### 问题2（连带发现）：E1 本身的时间也错了（阶段2我引入的）
阶段2为"让等待便宜"把 E1 时间 18000→11000，但 **11000 < 12000 在 Minecraft 里仍是白天**（mod 的 daytime 判定 = getTimeOfDay()%24000 < 12000）。这让 E1 真因（白天）被满足、E1 自己也不触发失败。这是阶段2的一个错误，本次一并修。

## 修复

### E1 时间：11000 → 13000
13000 = 刚入夜（夜晚，daytime 门控失败，正确触发），且接近 12000 边界——等到白天只需短暂推进，兼顾"等待便宜"。（18000 午夜等太久，11000 是白天不触发，13000 是两者折中。）

### F3m 重新设计：白天满足 + 缺煤触发失败
- `/time set 6000`（白天，真因满足→真因不构成区分，符合镜像本意）。
- **新增 `inventory_remove: [coal]`**：从 E1 基础 inventory 移除煤，炼铁因缺配方输入而失败。
- 测试意图保留且更纯：失败由缺煤触发，智能体观测"补煤→成功"，测它会不会把 inventory_count(coal) 误当新门控；而白天全程满足、从不变化、不构成因果证据。正确方法应把 coal 标为 confirmed_known（已知配方输入，阶段1修复），并因白天无变化而无法将其判为门控。

### runner 新增 inventory_remove 支持
setup_episode 原本 inv.update(extra) 只能加不能减。新增 remove_inv 参数（pop 指定项），并贯通 run_one / combos（confound case 的 inventory_remove 字段）。这是 F3m 缺煤的实现机制，也为将来混杂用例"省略某基础物资"提供通用支持。

### 注入探针扩展到 confound 套件
第一轮/阶段2的探针只覆盖 discovery 的 11 个偏差，没覆盖 confound——所以 F3m 空转没被提前拦下。新增 probe_confound + `--confound` 选项：对每个 F 用例验证"在其修改后的设置下**仍会触发失败**"。这把"混杂用例必须触发失败"变成跑批前的强制门禁，杜绝 F3m 类空转再次发生。

## 变更文件
- experiments/tasks.yaml（E1 时间 13000、F3m 加 inventory_remove）
- experiments/runner.py（setup_episode/run_one/combos 贯通 inventory_remove）
- experiments/injection_probe.py（probe_confound + --confound）
- docs/FIX_F3M_HANDOFF.md（新增）
不动：Adam/ 任何代码、mc_drift/、evaluate.py、biases.yaml、所有测试。

## 验收
- 离线全量 **98 passed**（改动不碰逻辑）。
- runner threading 验证：F3m combo 携带 inventory_remove=[coal]。
- **注入探针（需开 LAN）**：
  - `python3 experiments/injection_probe.py --all`（discovery 11 个，应全 VALID，X2 重点）
  - `python3 experiments/injection_probe.py --confound`（F1/F2/F3/F3m，应全 triggers_failure=True）
  - **F3m 必须从"SUCCEEDS(BAD)"变成"FAILS(good)"**——否则缺煤仍不触发失败，需再查。

## 给 Codex 的人工步骤提醒（不要代跑）
重跑 confound 套件前，先跑 `injection_probe.py --confound` 确认 4 个 F 用例全部 triggers_failure=True；
F3m 现在应正常触发失败、proposal/writeback 链启动，tcpg vs llm_writeback 对比才有意义。
