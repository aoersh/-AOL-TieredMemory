# 多目标 saved tensor 访问路径实验

日期：2026-09-22。选择 1、3、6 个目标 tensor 放在 CXL，其余候选固定在 DRAM。
当前没有设置受管 DRAM 硬上限，因此这是迁移量/队列成本实验，不是容量压力实验。

| 目标集合 | Direct CXL ms/step | Ranked ms/step | Ranked 迁移量/step | Direct−Ranked |
| --- | ---: | ---: | ---: | ---: |
| 1 个（ID 2） | 13.177 | 13.306 | 0.25 MiB | −0.130 ms |
| 3 个（ID 2,6,10） | 12.921 | 13.768 | 1.50 MiB | −0.847 ms |
| 6 个（ID 2,6,10,16,20,22） | 13.087 | 14.245 | 3.00 MiB | −1.158 ms |

每组 3 个独立进程，每进程预热 5 步、计时 20 步。结果是探索性统计，n=3，
不报告置信区间。ranked 没有迟到 unpack，但迁移管理开销随对象数增加。

该结果不能说明 Direct CXL 永远更好，因为没有 DRAM 容量限制，也没有制造
真实快层竞争。下一步应在受管池参考峰值的 25%、50%、75% 预算下，让 Direct
和 Ranked 处理相同候选集合，并记录预留、驻留、迁出、迟到和 byte-seconds。

普通用户的 `perf_event_paranoid=4` 仍阻止 raw/CPU PMU，AOL 尚未采集；需要
临时受限权限执行 PMU 命令，完成后恢复原值。

结果目录：`results/cpu-training-group-timing-0922/`；入口为
`run_group_matrix.py` 和 `analyze_groups.py`。

## 受管池预算后续结果

随后完成了明确预算的 20 个独立进程矩阵，详见
[预算实验报告](BUDGET_REPORT.md)。预算 0 与 Direct 基本重合；预算 1/2 MiB
分别迁移约 1/2 MiB 每步，平均 step 为 13.870/14.114 ms，而 Direct 为
13.276 ms。预算语义和迁移量已通过结果文件校验，但这是受管 tensor 池代理，
不是整进程 DRAM 容量压力，因此尚未证明容量受限场景下的收益边界。
