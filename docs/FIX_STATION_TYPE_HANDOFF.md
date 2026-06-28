# 修复包：station_type 编译契约 bug + K5 健壮性加固

## 故障(批次 A 跑批时连续 3 次异常,Codex 诊断正确)
ValueError: invalid literal for int() with base 10: 'crafting_table'
  at Adam/tcpg/compiler.py::_t_nearby_block

根因(Stage4/5 接口契约不匹配):
- K4 proposer 合法产生候选 station_type(property=type, value=crafting_table)——value 是块名;
- K5 compiler 的 TEMPLATES 把 station_type 复用了 _t_nearby_block;
- 而 _t_nearby_block 期望 value 是搜索半径整数,执行 int("crafting_table") 崩溃。
schema.json 的 station_type.value_type=block_name 是正确的,问题在 compiler 模板签名不匹配。

## 修复(两层)
1. 真正的修复:给 station_type 单独实现 _t_station_type(c, st)——value 作块名,
   半径内部固定 _STATION_RADIUS=3;I+ 获取并放置该站,I- 移开,只对可放置站(furnace/
   crafting_table/chest)可干预,否则 no_macro。TEMPLATES["station_type"] 改指它。
2. 整类 bug 的兜底(K5 健壮性契约):
   - _t_nearby_block 对非整数 value 改为抛 Infeasible("no_macro")(而非 int() 崩溃);
   - compile() 入口加边界守卫:任何模板抛出的非 Infeasible 异常一律降级为 no_macro
     并记 warning + compile_error 字段,绝不把异常传进 runtime 验证循环
     (否则会中断整个 episode、污染后验)。
   这样即便将来 LLM 产出 inventory_count>="lots" 这类形状错误候选,也只是该候选不可干预,
   不会让整个 run 崩。

## 变更文件
- Adam/tcpg/compiler.py(新增 _t_station_type + _STATION_RADIUS;
  _t_nearby_block 防御 int();compile() 边界守卫 + logging)
- tests/test_compiler.py(新增 2 个回归用例:station_type 块名编译、
  compile 永不因形状不符抛异常)
不动:proposer.py / posterior.py / ccg.py / runtime.py / executor.py /
schema.json / ADAM.py / mc_drift/ / experiments 实现。

## 测试
离线全量 **94 passed**(原 92 + 2 回归用例)。

## 重跑(从批次 A 继续;runner 断点续跑,已完成的自动跳过,失败的会重试)
合入并 git push 后:
   python3 -m pytest tests -m "not integration" -q     # 应 94 passed
   # 先删掉之前 3 个失败 run 的残目录(它们没有 summary.json,但保险起见)
   rm -rf experiments/runs/discovery/R1_tcpg_minimal_s0 \
          experiments/runs/discovery/R1_freedo_oracle_minimal_s0 \
          experiments/runs/discovery/R2_tcpg_minimal_s0
   python3 experiments/runner.py --suite discovery --all   # 从头补齐,已成功的跳过
