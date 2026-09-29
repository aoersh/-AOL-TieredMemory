# 迁移成本拆分证据（2026-09-29）

保存五条件诊断、15 个计时进程的配置、命令、摘要、正确性、诊断驻留事件及回归日志。
source/ 保存本轮执行器、测试和分析脚本；各 manifest 的 source_hashes 标识执行版本。
index.json 列出所有本轮结果文件的来源、大小、SHA256 及是否归档。
完整 steps.json、其他执行源码和日志仍保留在服务器 results/migration-ablation-*-0929/。
本目录不声称包含完整原始结果；重新分析时需要服务器原始 steps.json。

注意：summary.json 内时间字段值为毫秒，虽然内部键仍保留 *_ns 后缀；
原始 steps.json 的 *_ns 才是纳秒。后台任务墙钟不能与并行训练时间直接相加。

实验设计、统计与限制见 [迁移成本拆分报告](../../MIGRATION_ABLATION_REPORT.md)。
