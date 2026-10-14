# Archived CPU-training evidence

These files preserve measurements and the source snapshots actually executed.
Snapshots are historical artifacts, not the entry points for new experiments.
Use the scripts in the parent directory to run new experiments.

2026-10-14完成[科研审计](../RESEARCH_AUDIT_2026-10-14.md)。当前证据保留为原生
参考、公平负例及机制线索；停止旧attention迁移调参与高开销分配器策略搜索。
审计未新增测量；后续建议与停止条件见报告，当前状态见[HANDOFF](../HANDOFF.md)。

| Directory | Scope |
| --- | --- |
| [native-operator-1014](native-operator-1014/) | 默认分配器原生阶段缺页、三对CPU profiler诊断及五对无采样确认 |
| [native-placement-1014](native-placement-1014/) | 默认分配器原生DRAM/CXL、无副本或迁移，两条件各五独立计时进程 |
| [numa-v3](numa-v3/) | Original three-step MLP placement/correctness pilot |
| [e0-validated](e0-validated/) | Parameterized MLP and two Transformer correctness cases |
| [e1-diagnostic](e1-diagnostic/) | Eight DRAM/Direct/sync/async correctness and residency runs |
| [e2-timing](e2-timing/) | Forty independent timing processes, five paired repetitions per configuration |
| [closure-1009](closure-1009/) | Corrected 2×2 allocation pressure, fair DRAM/Direct/Prefetch placement, target-pack timing, audit results, and lightweight hashes |
| [capacity-pilot-1009](capacity-pilot-1009/) | 五条件受管副本池 32/64 MiB 容量、静态放置与同步需求迁移 |
| [capacity-selection-1012](capacity-selection-1012/) | 相同 32 MiB 额度下 ID10/ID34 对象选择及不迁移对照 |
| [backward-replay-1012](backward-replay-1012/) | 真实 softmax backward 输入重放，三个案例两放置，各五进程 |
| [large-placement-1012](large-placement-1012/) | sequence1024完整训练，128 MiB额度下初始ID34放置确认 |
| [framework-baseline-1013](framework-baseline-1013/) | 无保存副本native与受管全DRAM/Direct校准，各五独立进程 |
| [tensor-capacity-1013](tensor-capacity-1013/) | 无保存副本C10全张量64/128 MiB配额，七条件各三进程，不是整进程DRAM限额 |

2026-10-14第二阶段算子归档：无采样DRAM/CXL为76.803/90.470 ms，差+13.667 ms
[4.869,22.465]，反向慢但多数配对CXL缺页更少。profiler只作候选，完整trace/events
留本机；239原始hash、十项错误输入检查通过，见[报告](../NATIVE_OPERATOR_REPORT.md)。

2026-10-14新增原生放置证据：DRAM/CXL为74.245/89.284 ms，配对差+15.039 ms
[6.046,24.032]；九项JSON错误输入审计通过，155个原始hash复核。整体匿名放置
不等于纯张量访问或有限DRAM迁移策略，解释边界见
[原生报告](../NATIVE_PLACEMENT_REPORT.md)。

2026-10-13两套新增归档同样按diagnostic/timing/source/validation组织。保留内存
摘要与步骤锚点，完整内存映射和分配记录留本机；诊断零页触页不会进入性能计时。
各自均保留完整原始文件哈希，解释边界见 [本轮报告](../TENSOR_CAPACITY_REPORT.md)。

2026-10-12新增两套证据各按 `diagnostic/timing/source/validation` 组织，包含独立
进程均值、区间、配置、步骤锚点和完整原始文件哈希。完整步骤、日志、二进制留在
本机 `results/`，不能仅凭锚点重算统计。解释结论前请读
[收敛报告的比较对象与限制](../BACKWARD_REPLAY_REPORT.md)。

The new archives are copied from the matching `results/cpu-training-*` directories.
They include per-run manifests, source snapshots, raw step/event data, summaries,
and available figures. Build/cache files are omitted; `.log` files are preserved
as `.log.txt` to avoid the repository's generated-log ignore rule. Commands and
absolute/local output paths retain their original values for provenance.
Every archived manifest's source hashes were checked after copying.

Read the [E1/E2 report](../E1_E2_REPORT.md) before interpreting timing results.
The async implementation uses eager FIFO scheduling and exhibits demand-order
inversion; its timings do not establish a benefit-aware policy or a comparison
against optimized Always-Prefetch/TierTrain. Diagnostic timings are not performance
measurements. Source snapshots may differ between experiment batches.

## 2026-09-29 迁移成本拆分

新增 [五条件对照证据](migration-ablation-0929/README.md)：15 个计时进程、五个诊断、
结果摘要、运行配置、驻留证据与校验清单。完整逐步数据保留在服务器结果目录。

## 2026-09-29 页面参数向量化

新增 [七条件向量化证据](migration-vectorized-0929/README.md)，包括 21 个计时进程、
七个诊断、30 项回归记录和执行版本。局部构造加速尚未转化为稳定整步加速。

## 2026-09-29 固定延迟时机对照

新增 [六条件时机证据](migration-timing-0929/README.md)：18 个计时进程、1800 个步骤、
六个诊断、预设延迟协议和 33 项回归记录。近似对齐启动后，系统调用差异仍保留。

## 2026-09-29 C 内计时与同节点对照

新增 [native 测量证据](migration-native-0929/README.md)：18 个训练计时进程、
六组诊断、三个独立调用进程及 35 项回归。Python 返回边界不是主要耗时；
后续内核采样已完成，归属修复与 LRU 自旋热点证据见下一项。

## 内核热点与线程归属修复（2026-09-29）

[migration-kernel-profile-0929](migration-kernel-profile-0929/README.md)：
三个进程的安全符号统计、源文件哈希、修正 worker 报告与去地址调用链。
不包含 kallsyms.txt 或原始 perf.data；样本 period 权重不等于函数墙钟。

## 2026-10-09 收敛实验

新增 [closure-1009](closure-1009/README.md)：修正后的分配压力 2×2 和公平 DRAM/Direct/Prefetch 三方实验。证据目录只保留 manifest、分析摘要、步骤锚点、源码快照和完整原始文件哈希；完整 `results/`、日志和二进制仍留在实验机器。早期不公平结果的排除清单也随本轮提交，不能将旧 verified-analysis 当作有效结论。
