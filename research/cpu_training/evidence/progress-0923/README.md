# 2026-09-23 进展证据归档

本目录保存预算、单对象、PEBS 覆盖、500 步阶段对齐及最终三方对照的
轻量结果摘要、命令、运行配置和正确性证据。执行版本以每次 manifest 的
source_hashes 为准；父目录当前脚本用于后续实验。

index.json 记录来源路径、大小和 SHA256。原始逐步 steps.json 在服务器
results/ 中保留，其哈希一并列入清单；完整 PEBS 二进制、地址日志等原始
数据也保留在服务器结果目录。本归档不声称包含完整原始数据。

解读结果请阅读 [三方对照与初步结论](../../PLACEMENT_CAUSE_REPORT.md)、
[阶段对齐](../../MATCHED_500_REPORT.md) 和 [测量审计](../../MEASUREMENT_AUDIT_0923.md)。
历史生命周期统计的局限与作废结论以审计报告为准。

CSV 归档换行统一为 LF；清单分别保留源文件 sha256 与 archive_sha256。
