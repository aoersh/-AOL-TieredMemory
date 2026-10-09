# 分配压力与公平放置对照（2026-10-09）

## 结论与适用范围

两项收敛实验已完成，当前 ID10 场景仍无预取赢家。减少前台新分配可降低
并发迁移调用和前台写入成本，但没有获得 LRU 锁墙钟贡献的因果分解。
初始 DRAM 放置也未显示可重复的整步优势；不能由此推断 CXL 硬件比 DRAM 快。
暂不引入复杂 ML，也不将这一负结果视为否定 AOL。

固定 CPU0–7 计算、CPU8 迁移，DRAM node0、真实 CXL node2，内核
6.8.12-138-soaralto，无全进程 DRAM 容量限制。训练为 FP32 Transformer，
batch8/sequence512/width128/heads4/layers2，仅目标 saved ID10 为 32 MiB。
未改内核、sysctl，未重复 sudo/perf 采样。

所有正式矩阵每条件三个独立进程，每进程 5 步预热和 100 步正式；先做每条件
三步独立正确性诊断，再按轮随机条件顺序。表中先求进程均值，再求三个进程均值。
差值区间为配对 t(df=2) 95% 区间，未校正多重比较；不能将 300 步当 300 次独立重复。

## 必须排除的旧结果

10 月 8 日早期压力脚本存在 nomigrate 误匹配、fresh 双重复制、条件特有 GC
或释放边界不公平的问题；早期 DRAM 参考把 ID10 放入复用池，而 CXL 每步新建。
之前据此给出的“迁移额外约 45 ms、分配约 50 ms、DRAM 慢 2.32 ms”等数字撤回。
这些原目录及其中旧 verified-analysis 均保留，但不再作为结论依据。
完整目录排除清单在 [轻量归档](evidence/closure-1009/invalid-prior-runs.json)。

修正后的 10 月 8 日压力 v4 通过协议审计，但回归测试日志完成时间仅晚于矩阵
完成时间 2.695 秒，测试本体耗时 1.452 秒；缺少导入/启动起点，不能排除短暂重叠。
不声称已证实干扰，也不与新批次合并；10 月 9 日专门串行重跑 v5 作为主要结果。

## 独立分配压力 2×2

目标为一个持续存活的 32 MiB node2 buffer。前台固定写入 8×4 MiB node0 副本，
fresh 每步新建，reuse 保留已触页映射，两者都只复制一次 32 MiB。
主线程与 CPU8 worker 经 barrier 启动；无迁移条件也保留同样 worker。
迁移条件先 2→0，前台结束并释放 fresh 映射后由同一 worker 执行 0→2。
源内容更新、等待、释放、迁回全部计入 step；初始化、检查与池关闭计入 total_run。
该微基准不执行 DNN。

| 条件 | 整轮 ms | 前台复制/创建 ms | 前向 native ms | 迁回阶段 ms | 正式每步缺页 |
|---|---:|---:|---:|---:|---:|
| fresh，无迁移 | 5.859 | 3.643 | 不适用 | 不适用 | 8192.04 |
| fresh，有迁移 | 41.736 | 8.143 | 18.992 | 19.331 | 8192.33 |
| reuse，无迁移 | 0.685 | 0.403 | 不适用 | 不适用 | 0.03 |
| reuse，有迁移 | 35.823 | 1.710 | 14.655 | 19.189 | 0.32 |

在有迁移条件中，fresh−reuse 的前向 native 调用差为 **4.337 ms [4.132,4.542]**。
实际重叠从 7.874 ms 降为 1.504 ms；相同写入字节不等于相同重叠窗口，
因此不能把这 4.337 ms 单独归于 LRU，复用同时改变缺页、地址、缓存与并发持续时间。

差中差定义为 `(fresh迁移−fresh无迁移)−(reuse迁移−reuse无迁移)`：

| 指标 | 差中差 ms [95% CI] |
|---|---|
| 前台复制/创建 | 3.192 [2.902,3.481] |
| 并发阶段直到 join | 1.139 [1.079,1.199] |
| 整轮，含迁回/释放 | 0.739 [0.110,1.368] |
| 完整 run 含初始化/关闭，除以全部 105 步 | 0.035 [−2.421,2.491] |

本批整轮差中差虽高于零，但完整运行摊销区间跨零；不声称完整运行有稳定的
额外交互收益。复用自身能节省分配成本，与“减少了多少迁移引入的成本”是不同问题。

主要原始结果：`results/closure-pressure-timing-1009-v5/verified-analysis/`；
诊断：`results/closure-pressure-diagnostic-1008-v4/`。执行源码与诊断逐文件哈希一致。

## 训练三方公平对照与目标创建分段

三条件均只复用非目标 node0 副本，池容量 **107118592 bytes（102.15625 MiB）**；
目标 ID10 始终每步新建 **33554432 bytes**，DRAM 目标也不进池。
Direct CXL 与 Prefetch 每步初始都在 node2；只有 Prefetch 搬 32 MiB 到 node0。
目标每步释放后重新创建，故不需要回迁；创建/释放开销保留在整步，不能把池初始化
从完整 run 隐去。DRAM 参考仅改变目标初始节点，并非整进程 DRAM-only 或理论上界。

| 批次 | DRAM ms/步 | Direct CXL ms/步 | Prefetch ms/步 |
|---|---:|---:|---:|
| 10-08，无目标分段，seed20261008 | 97.863 | 88.420 | 108.848 |
| 10-09，有目标分段，seed20261009 | 92.535 | 88.047 | 107.036 |

两个批次均各三个独立进程，分别分析；改变了日期、顺序种子和少量测量代码，
不能把跨批次绝对时间变化解释为优化效果。

10-09 只在目标 Buffer 构造周围加两个单调时钟和进程缺页计数，所有三条件同样
测量，开销保留在 step。该区间含 mmap/mbind/frombuffer/首次 copy，性能运行不做
逐页查询；它不是纯 memcpy 时间。副本创建完成后才提交迁移任务。

| 10-09 指标 | DRAM | Direct CXL | Prefetch |
|---|---:|---:|---:|
| 目标创建 ms | 2.649 | 2.914 | 2.951 |
| 目标创建缺页/步 | 8192 | 8192 | 8192 |
| forward 减目标创建 ms | 37.514 | 33.893 | 49.214 |
| backward ms | 51.215 | 50.276 | 53.718 |
| 完整 run / 105 步 ms | 97.839 | 93.143 | 112.254 |

DRAM 的目标创建比 CXL **快 0.266 ms [0.101,0.430]**，不支持“DRAM 目标创建
更慢导致整步更慢”。剩余 forward 仍包括其他副本、分配、调度及计算，不能称为纯算子时间。
DRAM−CXL 的整步差本批为 **4.488 ms [−0.515,9.491]**，相较 10-08 的 9.443 ms
明显缩小且区间跨零；完整 run 摊销差为 4.696 ms [0.291,9.101]。两种口径均报告，
不选择性宣称稳定 CXL 硬件优势，也不声称已经查明 DRAM 劣势的根因。

Prefetch−Direct CXL 为 **18.989 ms [12.322,25.655]，约慢 21.6%**；完整 run
摊销差为 **19.111 ms [12.858,25.365]**。约 15.320 ms 的差位于除目标创建外的
forward，区间 [11.999,18.642]；native 墙钟/CPU 为 36.341/36.239 ms，
需求处等待仅 0.040 ms，全部正式步骤无迟到。与之前一样，“及时完成”不等于免费重叠。

原始结果：`results/closure-placement-{diagnostic,timing}-1008-v2/` 和
`results/closure-placement-pack-{diagnostic,timing}-1009-v1/`，各自使用 verified-analysis。

## 验证、复现与后续边界

新三方诊断 loss/梯度/更新后参数最大绝对误差为 0；初始及使用时驻留、
目标每步 fresh、非目标池容量、释放生命周期、源码/环境/选择轨迹与 loss 检查通过。
35 项回归通过；另外五项内存故障注入确认审计会拒绝 nomigrate 实际迁移、
缺少迁回字节、错误时间等式、DRAM 目标入池及越过 forward 的目标计时。
没有改写原始输入。回归运行在全部正式计时结束之后，有 wall/monotonic 起止记录。

从仓库根目录运行，输出目录必须不存在：

```bash
python3 research/cpu_training/run_closure_matrix.py pressure results/pressure-diag-new --diagnostic
python3 research/cpu_training/run_closure_matrix.py pressure results/pressure-time-new --diagnostic-reference results/pressure-diag-new
python3 research/cpu_training/analyze_closure.py results/pressure-time-new --output results/pressure-time-new/verified-analysis --diagnostic-reference results/pressure-diag-new
python3 research/cpu_training/run_closure_matrix.py placement results/placement-diag-new --diagnostic --measure-target-pack --seed 20261009
python3 research/cpu_training/run_closure_matrix.py placement results/placement-time-new --diagnostic-reference results/placement-diag-new --measure-target-pack --seed 20261009
python3 research/cpu_training/analyze_closure.py results/placement-time-new --output results/placement-time-new/verified-analysis --diagnostic-reference results/placement-diag-new
```

本轮停止重复调 ID10 的迁移参数。当前已有可用的负结果与分配干扰证据，但尚无
支持选择性预取策略或 AOL 收益预测的正负动作边界。若继续推进，先做有限的实际
backward 算子重放（同一保存值、相同线程、DRAM/CXL 两放置，初始化/复制单列并
纳入完整成本），判断是否存在可重复的访问收益；这不是本轮已完成实验。
只在访问收益存在时扩展另一个真实 tensor/shape，并回到训练端到端验证。
缓存、分配器/PTE/LRU 的分别因果贡献仍未确定，不直接改内核或把相关性包装成收益模型。

轻量证据：[evidence/closure-1009](evidence/closure-1009/README.md)。原始 steps、日志和二进制
留在 results；归档含各进程 manifest、运行账本、步骤锚点、分析、去重执行源码与完整文件哈希。
