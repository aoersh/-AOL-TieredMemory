# 有限 DRAM 副本池阶段证据

10-09 原矩阵已完整结束，10-12 检查断点后追加审计，不重复计时。
结果与适用范围见 [容量报告](../../CAPACITY_PILOT_REPORT.md)。

- `diagnostic/`：五条件各三步，数值和页面位置验证。
- `timing/`：五条件各三次独立进程，5预热+100正式；追加分析归档为 verified-analysis。
- `source/`：实际执行源码、历史runner和分析源码；版本相异的同名文件加hash后缀。
- `raw-checksums.json`：所有原始文件哈希，路径从仓库根开始。
- steps-anchors/capacity-anchors：仅第一步、第六步和最后一步。完整数据留本机 results，
  锚点不足以独立重新计算全部统计。

不包括二进制或完整日志。本轮旧manifest没有归档迁移C库hash/矩阵环境快照，
不能事后补成当时记录；此限制已在报告披露。新对象选择矩阵已补齐这些记录。
原始数据为 results/capacity-pilot-{diagnostic-1009-v3,timing-1009-v1}/。
同期复核及38项回归、五项审计故障注入见 [新矩阵validation](../capacity-selection-1012/validation/)。
