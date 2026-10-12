# sequence1024完整训练初始放置确认（2026-10-12）

承接128 MiB重放中发现的副本创建收益，预设只追加这一组完整网络确认。
Direct和pack34都允许128 MiB受管DRAM额度，其余受管副本CXL；没有页面迁移。
每步全部合格副本396.3125 MiB，参数/输入/梯度不在额度范围。

- diagnostic：两组三步，数值误差0，初始/使用驻留和释放通过。
- timing：两条件各五独立进程，5预热+100正式，随机串行。
- source：实际执行及分析源码。
- validation：38项回归、网络容量/shape/释放故障注入拒绝检查。
- raw-checksums：完整原始文件SHA256与字节数；完整steps与日志留本机results。
- steps-anchors/capacity-anchors：首步、第六步和末步，不能独立重算完整统计。

Direct284.384/pack34 284.565 ms，差+0.180 ms [−2.110,2.470]，
配对t(df=4)区间在±3%门槛内。forward更快但backward更慢，没有实用整步收益。
完整run含设置、全部预热与关闭，差值同样跨零。
见 [收敛报告及停止决定](../../BACKWARD_REPLAY_REPORT.md)。
原始结果：results/large-placement-{diagnostic,timing}-1012-v1/。
