MC-Drift Phase 0-4 Final Review

Date: 2026-06-28
World: `New World0609`
LAN used for final validation: `34199`

Scope

- Phase 0-2:
  - task manifest and labels
  - datapack recipe overrides
  - datapack mining tag override for coal
- Phase 3-4:
  - Fabric mod skeleton
  - config loading and reload command
  - truth logging
  - block-break y-level gates for U17/U19/U21

Static checks

- `python3 -m mc_drift.generator.validate_tasks mc_drift/tasks/u_tasks_final.yaml --labels mc_drift/tasks/u_tasks_labels.csv`
  - OK
- `python3 -m pytest mc_drift/tests/test_phase0_2_static.py mc_drift/tests/test_phase3_4_static.py -q`
  - `4 passed`
- Datapack build result:
  - `implemented_tasks = 11`
  - `unsupported_tasks = 20`
- Fabric config build result:
  - `total tasks = 31`
  - `active block-break gates = 3`

Task/action normalization

- Updated U-task actions to match existing IaP/CCG action naming:
  - `craftFence`, `craftBoat`, `gatherCoalOre`
  - `mineGoldOre`, `mineDiamondOre`, `mineRedstoneOre`
  - `smeltRawIron`
- Updated U20 from `smeltRawIron` to `smeltRawGold` with goal `gold_ingot`.

True-machine validation

Phase 0-2 datapack

- `/function iap_drift:status` reports implemented tasks:
  - `U00, U02, U03, U05, U06, U07, U08, U11, U16, U22, U24`
- U00 `craftFence`:
  - Mineflayer `recipesFor()` does not reflect datapack recipes, so validation used manual crafting-grid placement.
  - With the installed datapack pattern `PPP / PSP / PPP`, `8 oak_planks + 1 stick` produces `3 oak_fence`.
  - With fewer planks, crafting fails.
- U16 `gatherCoalOre`:
  - Reused the same coal `needs_stone_tool` gate already present in `mc_drift_R5`.
  - `wooden_pickaxe`: ore breaks but drops no coal.
  - `stone_pickaxe`: ore breaks and coal is obtained.

Phase 3-4 Fabric gate

- `/iapdrift reload` succeeded.
- Truth log path:
  - `.minecraft/iap_drift_logs/truth.jsonl`
- U17 `mineGoldOre`:
  - shallow `y=67`: blocked
  - deep `y=-15`: allowed, `raw_gold` obtained
- U19 `mineDiamondOre`:
  - shallow `y=67`: blocked
  - deep `y=-11`: allowed, `diamond` obtained
- U21 `mineRedstoneOre`:
  - shallow `y=67`: blocked
  - deep `y=-13`: allowed, `redstone` obtained
- After config reload, truth log entries use normalized action names:
  - `mineGoldOre`, `mineDiamondOre`, `mineRedstoneOre`

Notes

- Existing datapacks `mc_drift_R*` and `mc_drift_C*` were kept in the world and reused where they matched U-task semantics.
- The optional Mineflayer `collectblock` plugin is now loaded defensively so missing local builds do not block validation.
