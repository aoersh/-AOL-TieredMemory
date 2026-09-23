# 受管 tensor 池预算实验

日期：2026-09-22。该实验比较所有目标 tensor 保持 CXL（Direct）与在固定
受管 tensor 池预算内迁回 DRAM（budget）。预算只限制本实验创建的独立
saved-tensor buffer，**不是整进程 DRAM 限额**，因此结果用于验证迁移成本和
预算语义，不能直接声称已经制造了系统级容量压力。

目标 ID 为 `2,6,10,16,20,22`，每个条件 5 个独立进程，每进程预热 5 步、
计时 20 步。CPU、模型、线程和随机种子固定；结果目录为
`results/cpu-training-budget-matrix-0922/`，汇总为该目录的 `summary.json`。

| 条件 | 平均 step (ms) | 95% CI (ms) | 迁移 MiB/step | 等待 ms/step | 迁移次数/step |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct（预算不迁移） | 13.276 | ±0.424 | 0 | 0 | 0 |
| budget 0 MiB | 13.187 | ±0.245 | 0 | 0 | 0 |
| budget 1 MiB | 13.870 | ±0.265 | 1.0 | 0.033 | 4 |
| budget 2 MiB | 14.114 | ±0.327 | 2.0 | 0.036 | 5 |

区间采用五个独立进程均值的 Student t 区间（自由度 4）；不是将同一进程的
20 步视作独立样本。预算分配按 pack 顺序先到先得，工作队列为 FIFO；
它不是 ranked 历史需求优先级，也没有预算回收/驱逐。每步累计迁入上限
只约束指定六个目标，非目标候选仍在 DRAM。

所有 20 个进程均成功退出，未出现迟到 unpack。budget 0 与 Direct 的差异
处于本轮噪声范围内；budget 1/2 的迁移量符合预算，并出现约 0.6/0.8 ms
的平均 step 增量。该增量支持“选择性迁移会带来可测成本”，但还不能回答
真实 DRAM 紧张时是否值得预取，因为非目标内存和整进程匿名页没有受到容量
限制。

因此当前结论仍是：已确认可控的预算代理和迁移成本，尚未观察到稳定的
预取优于 Direct 的场景，也不进入 AOL 驱动策略实现。下一步应先在相同
预算代理下完成受管对象的 Direct、Always-Prefetch 和固定选择策略对照，
并记录预取覆盖、未命中和 DRAM 驻留；若仍无稳定边界，再转向 AOL 只做
离线解释性分析，不伪造收益结论。

运行：

```bash
python3 research/cpu_training/run_budget_matrix.py \
  results/cpu-training-budget-matrix-0922 --repeats 5 --steps 20 --warmup 5
python3 research/cpu_training/analyze_budget.py \
  results/cpu-training-budget-matrix-0922
```

## PMU 探针与验证

系统 perf 包装器缺少当前内核对应工具，已改用 `kernel/build-perf/perf`。
该工具仍因 `perf_event_paranoid=4` 拒绝 cycles/instructions；`sudo -n true`
提示需要密码。尚未取得本轮 AOL/PMU 数据。用户可仅以 sudo 执行项目 perf，
无需为了这一探针修改全局 sysctl；全进程计数只能检查事件可用性，不能
当作 tensor 级 AOL。

ranked、async、demand、边界测试共 14 项以及仓库测试 9 项通过；
Python 编译及 `git diff --check` 通过。
