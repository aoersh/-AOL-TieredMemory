# Archived CPU-training evidence

These files preserve measurements and the source snapshots actually executed.
Snapshots are historical artifacts, not the entry points for new experiments.
Use the scripts in the parent directory to run new experiments.

| Directory | Scope |
| --- | --- |
| [numa-v3](numa-v3/) | Original three-step MLP placement/correctness pilot |
| [e0-validated](e0-validated/) | Parameterized MLP and two Transformer correctness cases |
| [e1-diagnostic](e1-diagnostic/) | Eight DRAM/Direct/sync/async correctness and residency runs |
| [e2-timing](e2-timing/) | Forty independent timing processes, five paired repetitions per configuration |

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
