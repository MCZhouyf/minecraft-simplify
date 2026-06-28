# IAP MC-Drift Phase 0–2

这个代码包实现你当前方案中的前三个阶段：

- **阶段 0**：冻结 `u_tasks_final.yaml`，校验任务、谓词、标签。
- **阶段 1**：为 `resource_update` 任务生成 datapack recipe overrides。
- **阶段 2**：为 `U16 / MineCoal / held_tool(tier) >= stone` 生成 block tag override，使煤矿需要石级或更高等级工具。

本包主要覆盖 `minecraft-simplify` 仓库。建议先把它解压到 `minecraft-simplify` 根目录；之后只把 `mc_drift/tasks/u_tasks_final.yaml` 同步到 `IAP-Agent` 侧。

## 安装

```bash
cd minecraft-simplify
pip install -r mc_drift/requirements-phase0-2.txt
```

## 运行阶段 0 校验

```bash
python -m mc_drift.generator.validate_tasks mc_drift/tasks/u_tasks_final.yaml \
  --labels mc_drift/tasks/u_tasks_labels.csv
```

期望输出包含：

```text
MC-Drift validation: OK
tasks: 31
```

## 运行阶段 1–2 datapack 生成

```bash
python -m mc_drift.generator.build_datapack \
  --tasks mc_drift/tasks/u_tasks_final.yaml \
  --labels mc_drift/tasks/u_tasks_labels.csv \
  --out mc_drift/out/datapacks \
  --pack-name iap_phase0_2 \
  --pack-format 10
```

输出 datapack：

```text
mc_drift/out/datapacks/iap_phase0_2/
```

其中包括：

```text
pack.mcmeta
data/minecraft/recipes/*.json
data/minecraft/tags/blocks/needs_stone_tool.json
data/iap_drift/manifest.json
data/iap_drift/phase0_2_summary.json
```

## 安装到 Minecraft 世界

```bash
python -m mc_drift.generator.install_datapack \
  --pack mc_drift/out/datapacks/iap_phase0_2 \
  --world "/path/to/.minecraft/saves/YOUR_WORLD"
```

进入世界后执行：

```text
/reload
/datapack list
/function iap_drift:status
```

## 静态测试

```bash
python -m pytest mc_drift/tests/test_phase0_2_static.py -q
```

## 阶段 0–2 实现范围

已实现：

- U00, U02, U03, U05, U06, U07, U08, U11, U22, U24: recipe overrides
- U16: `needs_stone_tool` tag override for coal ore

未实现，需要 Fabric 后续阶段：

- situational_discovery
- boundary_update
- held_item(type)=... crafting gate
- runtime truth logging
- Mineflayer/IAP runner integration

## 重要设计说明

1. `u_tasks_final.yaml` 是唯一任务真值 manifest。
2. `u_tasks_labels.csv` 只用于实验分组，不应暴露给 agent。
3. `manifest.json` 记录了哪些任务在阶段 0–2 中已经由 datapack 实现，哪些任务要留到 Fabric 阶段。
4. 对于部分资源任务，ground truth 与原版配方数量一致；这些任务可以作为 sanity/control，也可以后续提高阈值以形成更明显 drift。
