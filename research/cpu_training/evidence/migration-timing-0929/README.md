# 固定延迟时机对照证据（2026-09-29）

保存六条件、18 个计时进程的摘要、进程均值 CSV、命令、配置、日志、校验记录与执行源码；
另有六个诊断的正确性和页面驻留事件。protocol.json 记录预设 5.5 ms 延迟及来源。
source/ 保存本轮实现、测试和分析器。index.json 记录运行文件来源、大小、SHA256，
以及是否归档。全部逐步 task_timeline 在服务器 results/migration-timing-control-0929/
各进程 steps.json 中；本目录只记录其哈希，不声称保存完整逐步原始数据。

summary.json 和 process_means.csv 时间单位为毫秒（_ms）；原始 steps.json 为纳秒。
CSV 空值表示该条件不适用，不表示零成本。validation.log 包含 33 项回归与编译检查。
系统调用与计算窗口重叠不能直接解释为因果干扰。结果与限制见
[迁移时机对照报告](../../MIGRATION_TIMING_REPORT.md)。
