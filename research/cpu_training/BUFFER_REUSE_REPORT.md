# 非目标 DRAM 缓冲复用干预（2026-09-30）

## 结论

完成 Direct/Prefetch × fresh/reuse_dram 四条件、各三次独立进程的随机区组实验，
每进程 5 步预热 + 100 步正式。先完成四组三步数值/驻留诊断与 32 项回归。
复用降低两条路径的开销，且 Prefetch 降低更多；但相同复用条件下，
Prefetch 仍比 Direct 慢 **18.900 ms/步**，95% 配对 t 区间 **[12.946, 24.854] ms**。
仍未发现稳定预取赢家，不进入复杂 ML，也不据此否定 AOL。

预取额外成本的差中差为 **9.513 ms/步 [4.581, 14.445]**。它支持当前
保存张量实现中的分配/生命周期管理会放大预取成本，但不能直接解释为消除
9.513 ms LRU 自旋：本轮没有新内核采样，复用还改变地址、缓存和内存保留状态。
原有约 41% LRU 自旋仍是内核周期样本权重，不是墙钟百分比。

## 最小干预与公平性

- 保持 CPU eager FP32 Transformer，batch8/sequence512/width128/heads4/layers2、
  SGD、种子 20260921；计算 CPU0–7，迁移 CPU8；进程默认内存绑定 node0。
- 仅 ID10（32 MiB，8192 个 4 KiB 页）初始在 CXL node2。所有模式都继续
  每步为它新建 mmap/mbind/copy，Prefetch 每步 node2→node0 搬 32 MiB；Direct 不搬。
  两种 buffer policy 下目标起始驻留及同一访问路径的迁移量相同。
- 新增 `--buffer-policy reuse_dram`：只保留并复用**非目标 node0** 副本映射，
  共 102.15625 MiB；每步仍把当前 tensor 内容完整复制进去。目标不进入池，
  因而没有需移出计时的反向迁回或目标重置。没有省略本来应该发生的回迁。
- 第一步按实际 pack 顺序建池并首次触页，全部算入该步；后续复用，正式阶段
  新建受管映射从 134.15625 降为 32 MiB/步。不是预先知道未来 trace 的离线分配。
- Prefetch 两条件都使用相同 ranked 执行器及 `real_vectorized_native`。第一步
  同步校准，之后按上一轮需求排序，未增加 sleep、计算或改变目标迁移参数。
- `paths.begin()`、状态重置及 lease 检查都在整步计时内。记录首次建池/触页、
  预热、执行器启动、池关闭；完整运行和外层子进程时间另外报告。

本轮是保存副本实现的分配干预，不是全进程 DRAM 容量约束，也不是完整
TierTrain/SoarAlto 策略。池保留 DRAM 是干预的一部分，不能声称没有内存代价。

## 生命周期与正确性

每个 saved ID 对应一个 node0 槽位。`torch.frombuffer` 持有 ctypes backing owner，
owner 通过 finalizer 在最后一个共享 storage 引用释放后解除 lease。单个 Packed
对象或 Tensor 的销毁不是可复用依据；view、detach、storage、NumPy 别名均可延长 lease。
仍有 lease 或 shape/dtype/node 签名不符时直接失败，不覆盖活对象，也不静默退回新分配。
所有异步任务 barrier 完成、图释放之后检查池空闲；运行退出时显式关闭池映射。

四组诊断相对于 native 的 loss、全部梯度和更新后参数最大绝对误差都是 0。
每步所有候选初始及 unpack 时逐页检查：ID10 初始 node2，Prefetch unpack node0，
Direct unpack node2，其余候选始终 node0。重复解包、别名、原地修改拒绝、
异步引用及失败传播等共 32 项回归通过，其中 6 项为新增池测试。
分析器另外拒绝六类故意破坏的记录：迁移量错误、初始节点错误、复用后重新分配、
池未关闭、loss 不一致、整步计时缺项；原始记录没有被修改。

早期 `results/reuse-pool-diagnostic-0930/` 的两个进程只作开发冒烟检查，
没有进程默认 node0 绑定，ranked 也未显式选 native 包装器，不用于下述比较。
正式诊断与计时来自 `buffer-reuse-{diagnostic,timing}-0930-v1`，源码及 C 库哈希一致。

## 三轮无采样性能结果

均为三个进程均值的均值。时间单位 ms；每进程 100 个正式步骤不是 100 个独立重复。

| 条件 | 整步 | 前向 | 反向（含调度） | C 内迁移墙钟 | C 内线程 CPU | minor faults/步 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fresh Direct | 104.743 | 44.664 | 59.112 | 不适用 | 不适用 | 133635.8 |
| fresh Prefetch | 133.156 | 71.350 | 60.635 | 48.867 | 48.662 | 126800.5 |
| reuse_dram Direct | 88.560 | 36.966 | 50.649 | 不适用 | 不适用 | 108296.3 |
| reuse_dram Prefetch | 107.460 | 51.760 | 54.542 | 36.301 | 36.227 | 106115.9 |

正式步骤合计 1200，Prefetch 合计 600 次 × 32 MiB；正式步骤 late_unpacks 全为 0，
major faults 全为 0。`ru_minflt` 是进程全部线程的缺页事件计数，不是独立物理页数，
不能直接乘 4 KiB 当成唯一分配字节数。它还包含非受管训练张量的分配/访问。

### 配对效应

随机种子 20260930；每轮四条件顺序独立打乱，同轮进程均值配对。三次重复的
t 区间使用 df=2，未做多重比较校正；不把单批次小样本当成跨机器稳定结论。

| 差值定义 | 均值 ms | 95% 区间 ms |
| --- | ---: | --- |
| fresh Prefetch − fresh Direct | 28.413 | [19.277, 37.549] |
| reuse Prefetch − reuse Direct | 18.900 | [12.946, 24.854] |
| fresh Direct − reuse Direct | 16.183 | [15.688, 16.678] |
| fresh Prefetch − reuse Prefetch | 25.696 | [20.999, 30.393] |
| (fresh P − fresh D) − (reuse P − reuse D) | 9.513 | [4.581, 14.445] |
| fresh P C 内墙钟 − reuse P C 内墙钟 | 12.567 | [11.000, 14.134] |
| fresh P C 内 CPU − reuse P C 内 CPU | 12.435 | [10.799, 14.072] |

差中差的三轮值为 8.461、8.275、11.803 ms。前向预取额外成本从 26.686 降到
14.794 ms，差 11.892 ms；反向及其余阶段抵消一部分，所以不能把前向改善和
C 内迁移改善相加。后者与前台有重叠。两种 Prefetch 需求等待约 0.044/0.040 ms，
仍不能仅凭“及时完成、等待很少”推断整步有收益。

## 初始化、释放与完整成本

`total_run` 从 optimizer/Paths 初始化前到关闭执行器和池之后，包含 5 步预热、
100 步正式、循环中的记账和检查。它不含 import、模型/输入创建及最终文件输出；
外层 runner 的子进程墙钟包含这些成本。下表全部以 **105 个实际训练步**摊销。

| 条件 | 首次池建映射/触页 ms | 关闭 ms | total_run/105 ms | 子进程墙钟/105 ms | 进程 peak RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh Direct | 不适用 | 0.101 | 109.787 | 122.228 | 622.724 |
| fresh Prefetch | 不适用 | 0.123 | 138.141 | 150.210 | 633.085 |
| reuse Direct | 12.280 | 4.778 | 93.678 | 105.853 | 637.178 |
| reuse Prefetch | 12.472 | 5.530 | 112.690 | 124.932 | 652.738 |

首次池指标包括首次副本复制，不是额外独立于训练的开销；已计入首步及 total_run，
不得再加一遍。池关闭不塞入最后一个正式步，已纳入完整运行/进程时间。
同复用条件下 Prefetch 在 total_run/105 上仍慢 19.012 ms [13.323,24.702]；
子进程墙钟/105 上慢 19.080 ms [13.908,24.252]，结论不依赖排除初始化或释放。

池保留 102.15625 MiB DRAM，但 peak RSS 差只有约 14.454/19.654 MiB，因为
原先 fresh 活对象也消耗内存、其他分配器及重叠峰值不同；peak RSS 差不能当成
池的容量大小，不能据此声称池只占用十几 MiB。

## 环境、限制与下一步

内核仍为 6.8.12-138-soaralto；numa_balancing=1、pte_scale=16、
perf_event_paranoid=-1、kptr_restrict=1。未修改内核、全局 sysctl 或 C 计时库。
`cxl list -M` 返回 0 但报告 mem0/mem1 解析失败，不能当成枚举成功；额外保存
sysfs，`cxl/region0 → dax_region0/dax0.0` 的 target_node=2、容量 64 GiB，
以及 mem0/mem1 的 CXL 设备路径。进程开始/结束 loadavg 已存档。

复用同时改变 mmap/mbind/首次触页、munmap 的频率以及 DRAM 保留/缓存/地址状态。
本轮证明的是这一**整体实现干预**降低开销，并减少相对 Direct 的额外预取成本。
还没有测量相同锁地址、锁持有者或干预后的 LRU 样本，不能把差中差或
12.567 ms C 内缩短全称为 LRU 自旋减少，更不能说消除了全部迁移/前台干扰。

下一步应二选一：在相同两种实现上做新的一组内核机制采样，或做固定迁移目标
加独立并发分配/预触页压力的控制，进一步分离 LRU、复制与其他分配成本。
这是新的干预核验，不是重跑/修复 09-29 的采样。保持原内核，不删除 lru_cache_disable。
若以后复用 ID10，必须另行加入可见的逐步 node0→node2 重置与迁移量记账；
本实现不能被改名为“全池双向复用”。AOL 特征与收益关联在执行器与收益边界明确后继续。

## 复现与证据

从 SoarAlto 根目录执行，所有输出目录必须不存在：

```bash
python3 research/cpu_training/test_buffer_reuse.py
python3 research/cpu_training/run_buffer_reuse.py results/buffer-reuse-diagnostic-new --diagnostic
python3 research/cpu_training/analyze_buffer_reuse.py results/buffer-reuse-diagnostic-new \
  --output results/buffer-reuse-diagnostic-new/verified-analysis
python3 research/cpu_training/run_buffer_reuse.py results/buffer-reuse-timing-new --steps 100 --repeats 3
python3 research/cpu_training/analyze_buffer_reuse.py results/buffer-reuse-timing-new \
  --output results/buffer-reuse-timing-new/verified-analysis \
  --diagnostic-reference results/buffer-reuse-diagnostic-new
```

32 项回归的完整命令与日志在 `results/buffer-reuse-validation-0930-v1/`。
本轮原始数据在 `results/buffer-reuse-{diagnostic,timing}-0930-v1/`；
计时有效分析为 `buffer-reuse-timing-0930-v1/verified-analysis/`。
轻量归档见 [evidence/buffer-reuse-0930](evidence/buffer-reuse-0930/README.md)。
原数据与旧采样均保留，不把原始 perf.data、内核地址或二进制加入轻量证据。

## 2026-09-30：复用后的内核机制核验完成

新完成四条件各三进程采样，219017 个样本、正式步骤内 202249 个，零丢样。
Prefetch worker 的每步 LRU 自旋权重由 66.051 降至 33.094 百万采样周期；
其余线程匿名缺页/LRU 由 551.995 降至 281.901，98% 以上发生在 native 调用期间。
copy_page 30.664→29.408 的差值区间跨零；前台 PTE 锁自旋也大幅下降。
新证据支持分配/迁移干扰机制，但仍不能把此前的整步改善全归为 LRU 墙钟收益。
性能结论沿用无采样结果，复用 Prefetch 仍慢于 Direct。下一步独立分配压力 2×2。
同内核/同启动符号快照经 196050 核心栈帧交叉核验；没有重跑旧采样或修改内核。
详见 [复用后的内核机制报告](BUFFER_REUSE_KERNEL_REPORT.md)。
