# 固定 32 MiB 的对象选择证据（2026-10-12）

比较Direct与两个同尺寸attention保存对象ID10/ID34的初始DRAM放置、同步需求迁入。
所有策略允许32 MiB受管副本额度；Direct可不用，其他副本保持CXL。
结果详见 [阶段报告](../../CAPACITY_PILOT_REPORT.md) 和 [阶段总结](../../DAILY_SUMMARY_2026-10-12.md)。

- `diagnostic/`：五组三步正确性、驻留、账本、选择核验，最大数值误差0。
- `timing/`：每条件三个独立进程，每进程5预热+100正式，随机串行。
- `source/`：实际源码和分析runner快照。
- `validation/`：38项回归与五项故障注入拒绝检查，全在计时结束后进行。
- `raw-checksums.json`：完整原始文件的SHA256与字节数；二进制与完整日志仅留本机。

每进程manifest包含C库和源码hash，矩阵environment包含NUMA与CXL映射。
summary的admitted_ids压缩为唯一集合，统计和三个进程均值保持原值。
steps-anchors/capacity-anchors仅含第一步、第六步和最后一步；不能只靠锚点重算均值。
完整原始结果：results/capacity-selection-{diagnostic,timing}-1012-v1/。

三个独立进程的配对t(df=2)区间未校正多重比较。初始放置区间跨零，尚无可靠
对象优先级；同步需求迁移两对象都慢约30 ms。租约积分不含初始创建过程和内核
临时staging，不是整进程DRAM物理驻留时间，也不是对强异步预取的否定。
