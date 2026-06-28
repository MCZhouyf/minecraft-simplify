# 第三轮 阶段0-1 交接：基线确认 + 分支 + 套件重命名映射

## 基线结论（已实拉 GitHub 核实）
当前 HEAD = ff37fb0（"rerun C1 C3 R1 R3 tcpg after execution timeout fixes"），离线 118 passed。
已含：abort 三分支修复（undo_fail/ctx_resync/ctx_unrestorable）+ 保底 min_verifications_per_cand
+ 方案B sanity 注入检测 + **执行超时修复（wait primitive 真实超时 max_checks/wait_ticks + 编译层
全天窗口判 no_macro）**。

**第三轮基于 ff37fb0 向前做，不回退**——回退会丢失 wait 超时修复（P1/C3 时间类偏差会重新卡死）。
ff37fb0 比旧的 9799cbe 多了超时修复 + 重跑数据，是更干净的基线。

重跑数据（超时修复后）：tcpg 仍仅 C2 gt_accepted=1，C1/C3/R1/R3/P1/E2 仍为 0。
→ 这证明执行层问题（卡死/误判abort）已修，但这些偏差的**深层结构问题**（抽象层级竞争、
  双池信号弱、真因没进池）非执行层可解——**这正是第三轮换套件的实证理由**。

## 阶段0-1 范围（最小、可验证、低风险）
本阶段**只做基线确认 + 分支 + 重命名映射记录**，不动偏差注入逻辑、不改 mod、不写 datapack。
套件的实质重构（空背包、新任务注入、R3 datapack、C3 夜晚 Java）留待后续阶段在真机上逐个验证落地，
避免一次性大改无法归因。

## 重命名映射（仅记录，本阶段不改注入）
资源输入型 Resource-Input（R1–R6）："提供什么"
- R1(←R1) craftFurnace 需催化剂砂 | R2(←R2) craftFence 需板≥8 | R3(←R3) 伪新物品
- R4(←P1) craftIronPickaxe 需先持button | R5(←C1) gatherCoalOre 需石镐 | R6(←C2) mineGoldOre 需钻石镐
情境约束型 Situational-Constraint（C1–C4）："在什么情况下"
- C1(←P2) 附近熔炉 | C2(←E3新增) 附近水 | C3(←E1) 夜晚 | C4(←E2/X1) 深度

注意：字母 C 从 capability 改指 Context/Constraint；原 capability 的挖煤/挖金→R5/R6（工具属"提供什么"）。

## 本阶段交付物
- docs/ROUND3_RENAME_MAP.md（重命名映射，供后续阶段查阅）
- 新分支 round3-suite-v2（从 ff37fb0 切）
- 确认离线 118 passed 不变
