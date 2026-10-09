# 10 月 8–9 日收敛实验轻量证据

结论与限定见 [实验报告](../../CLOSURE_REPORT_1009.md)。各矩阵保留独立分析，不跨批合并。

- 压力主要证据：closure-pressure-timing-1009-v5，12 个进程；10-08 v4 仅作历史参考，不能排除其末尾与测试导入短暂重叠。
- 公平训练参考：closure-placement-timing-1008-v2，9 个进程；带目标创建分段的独立确认：closure-placement-pack-timing-1009-v1，9 个进程。
- 三套 diagnostic 对应各自执行源码，正式运行前已验证数值、驻留与生命周期。
- invalid-prior-runs.json：早期无效结果的排除与撤回清单，原始目录保留。
- validation-*：本次 35 项回归及带时钟的运行记录；audit-rejection-tests.json：五项故障注入拒绝验证。
- raw-sha256.json：归档时所有原始文件的哈希及字节数；原始相对路径均从仓库根开始。
- sources/<sha256>.py 或 .c：去重后的实际执行/分析源码，用各进程 manifest 的 source_hashes 对应。
- step-anchors.json 只含第一步、第六步与最后一步，不足以独立重算均值；完整 steps 留在本机 results。
- 二进制、完整步骤和日志不复制进 Git，但保留哈希。各 verified-analysis/summary.json 含三个进程均值、配对差值与区间。

新增分段计时有少量开销；10-08 与 10-09 不构成计时开销的严格配对对照。
归档命令入口 archive_closure.py；不覆写已有输出目录，不修改原始结果。
