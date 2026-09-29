# 页面参数向量化证据（2026-09-29）

保存七条件、21 个计时进程的摘要、命令、配置、日志、校验记录与执行源码；
另有七个正确性诊断及目标页面驻留事件。source/ 保存本轮代码和分析实现。
index.json 列出所有运行产物的来源、大小、SHA256，以及是否已归档。
完整逐步 steps.json 仍在服务器 results/migration-vectorized-*-0929/ 中，
归档包含其哈希，不声称能仅用摘要重建完整逐步数据。

summary.json 时间字段统一为 _ms；后台任务与训练重叠，不可直接相加。
validation.log 记录 30 项回归、编译和差异检查；verification.json 记录恢复后审计。
用户反馈会话中断后发现所有实验产物完整，没有重跑或拼接部分计时。

结果与局限见 [向量化迁移报告](../../VECTORIZED_MIGRATION_REPORT.md)。
