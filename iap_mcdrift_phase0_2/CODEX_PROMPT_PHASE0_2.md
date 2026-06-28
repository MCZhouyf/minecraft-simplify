你是我的工程协作者。请在两个仓库中实现 MC-Drift 阶段 0–2。不要一次性实现 Fabric mod；本轮只做 task manifest/schema、datapack recipe overrides、以及 U16 的 mining tool-level datapack tag。

背景：
- Minecraft: Java 1.19.x LAN integrated server
- Agent: Mineflayer
- benchmark repo: https://github.com/MCZhouyf/minecraft-simplify
- agent repo: https://github.com/MCZhouyf/IAP-Agent
- 本 zip 解压后会提供 mc_drift/tasks、mc_drift/generator、mc_drift/tests 等文件。

执行步骤：

1. 在 minecraft-simplify 根目录解压本 zip，允许覆盖/新增 mc_drift 下的文件：
   unzip iap_mcdrift_phase0_2_code.zip -d .

2. 安装依赖：
   pip install -r mc_drift/requirements-phase0-2.txt

3. 运行阶段 0 校验：
   python -m mc_drift.generator.validate_tasks mc_drift/tasks/u_tasks_final.yaml --labels mc_drift/tasks/u_tasks_labels.csv

4. 运行阶段 1–2 datapack 生成：
   python -m mc_drift.generator.build_datapack      --tasks mc_drift/tasks/u_tasks_final.yaml      --labels mc_drift/tasks/u_tasks_labels.csv      --out mc_drift/out/datapacks      --pack-name iap_phase0_2      --pack-format 10

5. 运行静态测试：
   python -m pytest mc_drift/tests/test_phase0_2_static.py -q

6. 检查生成目录：
   mc_drift/out/datapacks/iap_phase0_2/
   必须存在：
   - pack.mcmeta
   - data/iap_drift/manifest.json
   - data/minecraft/recipes/oak_fence.json
   - data/minecraft/tags/blocks/needs_stone_tool.json

7. 把任务 manifest 同步到 IAP-Agent：
   mkdir -p ../IAP-Agent/mc_drift/tasks
   cp mc_drift/tasks/u_tasks_final.yaml ../IAP-Agent/mc_drift/tasks/
   cp mc_drift/tasks/u_tasks_labels.csv ../IAP-Agent/mc_drift/tasks/

8. 不要在这一轮实现 Fabric。只在 README 或 TODO 中标记下一阶段需要实现：
   - Fabric mod skeleton
   - PredicateEvaluator
   - mining y-level gate
   - crafting/smelting Mixin gate
   - truth log / public observation log separation

验收标准：
- validate_tasks 输出 OK
- pytest 全部通过
- datapack manifest 中 implemented_tasks = 11，unsupported_tasks = 20
- U00 recipe override 提高 oak_fence 对 oak_planks 的需求
- U16 needs_stone_tool tag 包含 minecraft:coal_ore 和 minecraft:deepslate_coal_ore

注意：
- 不要把 ground_truth 暴露给 agent runtime。
- u_tasks_labels.csv 只能用于评测分组。
- datapack pack_format 默认 10，适配 Minecraft 1.19–1.19.2；如果实际使用 1.19.3/1.19.4，请改 build 参数。
