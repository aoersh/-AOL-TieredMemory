# 真实 softmax backward 重放证据（2026-10-12）

读取 [阶段报告](../../BACKWARD_REPLAY_REPORT.md) 后解释本证据。

- diagnostic：三个案例两种放置各三步，逐元素误差0、逐页驻留、释放均通过。
- timing：六条件各五独立进程，5预热+100正式，随机串行，真实保存值和梯度hash一致。
- source：实际执行、runner和分析源码。
- step-anchors：第一步、第六步和最后一步，不能只靠锚点重算统计。
- raw-checksums：本机完整原始文件SHA256及字节数，路径从仓库根开始。
- validation：六项审计故障注入结果及后续回归记录。

原始根目录：results/backward-replay-{diagnostic,timing}-1012-v1/。
完整步骤与日志保留本机。本实验只重放真实SoftmaxBackward算子的一次使用，
fresh copy后的缓存状态没有人为flush；不是完整训练、纯内存访问延迟或迁移净收益。
最后一次数值检查包含在完整步骤和run，使用时间不含数值检查。
配对t(df=4)区间未校正多重比较。
