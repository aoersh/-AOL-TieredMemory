# 内核采样轻量证据（2026-09-29）

三组完整采样，无记录丢样；worker 样本数为 167 / 1066 / 1050。
源结果：results/migration-native-kernel-profile-0929/。
结论与局限见 ../../KERNEL_MIGRATION_PROFILE_REPORT.md。

- summary.json：原始输入哈希、逐线程样本数/period、符号权重及去地址的自旋调用链。
- worker-report.txt：修正后的 TID 独立聚合；worker-callgraph.txt：去地址后的调用链。
- manifest.json：各进程执行配置及源码/二进制哈希；本轮只检查 loss/任务计时等完整性，非额外梯度诊断。
- executed-runner.sh 为用户实际执行版本；corrected-runner.sh 为本轮修正版，未冒充已执行版本。
- analyzer/test 为当前分析与回归源码，analysis-completed.json 表示三组核验通过。

原始 perf.data、完整 steps.json 和 kallsyms.txt 留在本机。不得公开 kallsyms.txt。
百分比为线程整个采样生命周期的内核周期权重，含启动/预热，不能解释成函数墙钟或正式步骤占比。
校验哈希见 index.json；同节点样本少，未采到某函数不等于绝无该开销。
