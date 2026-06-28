# 第 7 阶段交付（实验跑批层）：tasks.yaml + runner + evaluate

## 交付文件
| 文件 | 说明 |
|---|---|
| experiments/tasks.yaml | 任务套件：3 个锚点坐标（唯一需按试验区调整处）、12 偏差回合配置（初始物资/矿石重放/前置命令）、三套件（discovery 108 / feedback_ladder 27 / confound 24，共 159 组合） |
| experiments/runner.py | 跑批器：单组合或 --all 批量，**断点续跑**（已有 summary.json 即跳过）；双机制偏差启停（C* 数据包切换 / 其余模组配置+热重载）；每偏差矿石按次重放；K7 全程落盘；--scripted 无钥冒烟 |
| experiments/evaluate.py | 聚合器：K7+候选终态 → table4_discovery.csv（兼作表 1 方法阶梯行）/ table3_feedback.csv / table7_confound.csv / lifelong_curve.csv / summary.json（含可选 K8 交叉核验覆盖率）；谓词级匹配（y 容差 8、时间窗重叠 ≥0.8、其余精确） |
| tests/test_evaluate_offline.py | 合成运行夹具上的聚合口径测试 |

离线全量：**88 passed**。关键设计：E1 任务在回合前置命令里打开 doDaylightCycle（否则 wait 宏永远等不到白天——时间被冻结）；每回合 kit 含 chest（暂存纪律）；discovery 套件先跑 seed 0（36 个运行）再补 seed 1/2，runner 续跑特性使分批无成本。
