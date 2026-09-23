# 单 saved tensor 访问路径实验

日期：2026-09-22。本轮在小 Transformer（batch=4、sequence=128、width=128、
heads=4、两层、FP32）中只把一个指定 saved tensor 放在 CXL，其他合格候选
全部绑定到 DRAM 0。比较 Direct CXL、需求时同步迁移和 pack-time 历史优先级
预取（ranked）。这是 Q1 的因果探测，不是容量压力实验。

## 目标对象

沿用诊断日志中的稳定 saved-tensor ID：

| ID | 形状 | 4 KiB 对齐迁移量/每步 |
| ---: | --- | ---: |
| 2 | `[4,128,128]` | 0.25 MiB |
| 6 | `[512,128]` | 0.25 MiB |
| 10 | `[4,4,128,128]` | 1.0 MiB |

ID 只在本固定 workload/固定执行图中有效，不能当作跨模型持久标识。实验
仍记录 shape、代际和候选原因；动态形状或算子顺序改变时必须重新校准。

## 正确性和计时

修复了单对象隔离器的状态错误：非目标 DRAM buffer 不再执行“迁回 DRAM”，
迁移后目标对象的当前节点也会更新为 0。修复前的
`results/cpu-training-single-timing-0922/` 全部作废；有效结果是
`results/cpu-training-single-timing-0922b/`。

有效诊断重新运行 Direct/sync/ranked 三种模式，均通过 native loss、梯度、
更新后参数、逐页驻留和映射释放检查，最大误差为 0。原地修改、异常传播、
优先级工作者等测试也通过。

有效计时为 3 个目标 × 3 个策略 × 5 个独立进程；每进程预热 5 步、计时 20 步，
策略顺序随机。统计为独立进程均值，Direct−ranked 区间为探索性 df=4 的
95% t 区间。

| 目标 | Direct CXL | 同步迁移 | Ranked 预取 | Direct−Ranked |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 13.054 ms | 13.647 ms | 13.410 ms | −0.356 ms，CI [−0.814, +0.102] |
| 6 | 13.109 ms | 13.436 ms | 13.616 ms | −0.507 ms，CI [−0.869, −0.145] |
| 10 | 13.216 ms | 14.296 ms | 13.781 ms | −0.565 ms，CI [−0.974, −0.156] |

同步迁移平均等待分别为 0.339、0.304、0.816 ms；ranked 的等待分别为
0.016、0.016、0.015 ms。ranked 在 pack 时提交目标迁移，能够隐藏大部分
迁移等待；但其整步时间仍没有胜过 Direct。目标 2 的差异区间跨 0，近似持平；
目标 6 和 10 中 Direct 小幅领先，不能从这个小工作集宣称普遍规律。

## 结论和限制

这轮没有观察到“某个目标明显更适合预取、另一个目标明显更适合 Direct”的
正向混合证据。它支持继续寻找窗口/大小边界，但尚不足以实现收益策略或 ML。
ranked 比全量预取更接近 Direct，说明对象选择和迁移窗口可能比队列排序更关键。

工作集仍小、容量充足，非目标数据固定在 DRAM，尚未测算整个训练进程的 CXL
流量，也没有加入 DRAM 压力。ID 不是跨模型对象标签。当前 step 时间包含 hooks、
受管 buffer 复制、必要迁移和策略管理成本，但不含诊断逐页查询。

## AOL/PMU 状态

已探测 Granite Rapids 事件配置和自带 `kernel/build-perf/perf`。当前
`perf_event_paranoid=4` 拒绝普通用户的 CPU/raw PMU 访问，AOL 尚未采集；
不能用失败或空计数替代 AOL 特征。后续需要在不改变实验条件的前提下临时
降低权限或使用具备 CAP_PERFMON 的受限 perf 包装，并在结束后恢复原值。

## 文件

- 诊断：`results/cpu-training-single-diagnostic-0922/`。
- 有效计时：`results/cpu-training-single-timing-0922b/`。
- 作废计时保留但不引用：`results/cpu-training-single-timing-0922/`。
- 运行入口：`run_single_matrix.py`、`analyze_single.py`。
