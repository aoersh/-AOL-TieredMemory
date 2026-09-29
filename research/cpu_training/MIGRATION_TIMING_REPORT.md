# 迁移开始时机对照（2026-09-29）

## 问题与预设方案

承接 [向量化报告](VECTORIZED_MIGRATION_REPORT.md)：向量化参数准备减少约 5.561 ms，
但 move_pages 墙钟增加约 5.523 ms，训练整步尚未稳定改善。本轮检验更早进入系统调用
是否改变与计算的重叠；不预设带宽、GIL 或页锁是原因，也不修改 AOL/ALTO 算法。

在第一次计时前固定延迟 5.5 ms，取前轮参数准备差的近似值。协议在每次输出目录
protocol.json 中保存，不能按本轮结果调整延迟或挑选重复。

六条件：

- direct：ID10 保持 CXL，不预取。
- real：原 Python/ctypes 参数准备和真实迁移，记录阶段时间戳。
- real_vectorized：向量化准备、立即搬页。
- real_vectorized_delay：向量化准备完成后 sleep 5.5 ms，再搬页。
- prepare_vectorized：只准备参数、不搬页。
- prepare_vectorized_delay：只准备参数、再 sleep 5.5 ms，不搬页；用于观察等待/调度影响。

所有新增等待都在任务和训练整步计时内。sleep 释放执行机会，不模拟原 Python 参数
构造的 CPU 工作或 GIL 行为；即使时机接近，也不是所有资源行为完全相同的反事实。
原默认迁移实现保留，新增参数只用于显式诊断对照。

## 数据与固定条件

大配置 Transformer：batch=8、sequence=512、width=128、heads=4、layers=2，FP32 SGD。
仅 saved tensor ID10（32 MiB，8192 页）初始位于 CXL node2，其他受管候选在 DRAM node0。
训练 CPU0–7、迁移 CPU8；保留 MPOL_BIND，未施加整进程 DRAM 上限。
内核 6.8.12-138-soaralto，numa_balancing=1，perf_event_paranoid=-1；未修改系统参数。

六条件各三次独立进程，每进程预热 5 步、正式 100 步；共 18 进程、1800 个正式步骤。
每轮随机排列条件（种子 20260929），串行执行。另有六个三步数值/驻留诊断。
统计基于三次进程均值，95% 配对 t 区间 df=2、未作多重比较校正。

每步保存同一 CLOCK_MONOTONIC 域内的训练起止、前向结束、反向起止以及任务时间线：
目标 buffer 可用、提交、任务开始、参数准备起止、等待起止、系统调用起止、任务结束、需求。
ready_ns 是 buffer 初始化/复制完成后记录的时间，不是 PyTorch 原张量产生时刻。
真实 sleep 耗时由墙钟记录，可能超过请求的 5.5 ms。

先检查延迟组系统调用相对 ready 的启动偏移是否接近旧版，再解读系统调用差异；
若时机未接近，则不能认为“等时刻仍有差异”已被检验。近似时机相近不等于统计等效。
系统调用墙钟与前向/反向的重叠只是时间交集，不能直接当作因果干扰量。

## 结果

全部 18 个计时进程、1800 个正式步骤完成；六组诊断数值最大误差均为零，驻留与释放检查通过。
33 项相关回归、Python 编译检查和 git diff --check 通过。计时源码快照与 manifest 哈希、
当前执行代码一致，完整 loss、对象选择和时间线检查通过。未发现失败记录。

### 同批结果

时间单位 ms。启动偏移为 move_pages 开始减去目标 buffer ready；未搬页条件不适用。

| 条件 | 整步训练 | 前向 | 实际等待 | 启动偏移 | 系统调用 | 迁移任务 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 102.696 | 43.268 | 0.000 | 不适用 | 0.000 | 0.000 |
| real | 140.554 | 77.486 | 0.001 | 6.358 | 44.800 | 51.602 |
| real_vectorized | 138.109 | 74.316 | 0.001 | 0.697 | 49.543 | 50.688 |
| real_vectorized_delay | 138.168 | 73.405 | 5.572 | 6.445 | 48.998 | 55.901 |
| prepare_vectorized | 105.872 | 45.537 | 0.001 | 不适用 | 0.000 | 0.000 |
| prepare_vectorized_delay | 104.971 | 44.740 | 5.569 | 不适用 | 0.000 | 0.000 |

不搬页控制的 migration=0 是事件分类，不表示准备/等待没有成本。真实迁移三组每步
均搬运 32 MiB，其他条件为零；这里是软件统计的成功迁移字节，不是 CXL 硬件流量。
所有正式步骤 late_unpacks=0，时间在后台任务和计算间重叠，不可把各列直接相加。

### 配对结果

| 比较（前者减后者） | 整步差异 ms | 95% t 区间 |
| --- | ---: | --- |
| real_vectorized_delay-real_vectorized | +0.060 | [-8.407, 8.526] |
| real_vectorized_delay-real | -2.386 | [-15.405, 10.634] |
| real_vectorized-real | -2.445 | [-10.337, 5.446] |
| real_vectorized_delay-direct | +35.472 | [31.832, 39.112] |
| prepare_vectorized_delay-prepare_vectorized | -0.901 | [-6.769, 4.967] |
| real-direct | +37.858 | [28.064, 47.652] |

1. **时机干预确实生效。** 延迟版相对立即向量化版，启动偏移增加 5.747 ms，
   95% 区间 [5.255, 6.240] ms；实际 sleep 平均 5.572 ms。
2. **与旧版的启动时间近似对齐。** 延迟版 ready 后 6.445 ms 开始，旧版为 6.358 ms；
   配对差异 +0.086 ms，区间 [−0.318, +0.490] ms。三个进程配对差为
   −0.0005、+0.274、−0.0145 ms。相对 step 的平均开始时间也接近：23.209 与
   23.334 ms。这是描述性近似对齐，不是预设等效性检验的证明。
3. **系统调用差异仍保留。** 延迟版比旧版慢 4.197 ms，区间 [2.306, 6.088] ms，
   三轮均为正。延迟版相对立即向量化版仅减少 0.546 ms，区间 [−2.632, 1.541] ms；
   未观察到此前约 5 ms 差异被等待操作抵消。
4. **没有稳定整步加速。** 延迟版相对立即向量化版差异 +0.060 ms，区间跨零；
   相对 Direct 则慢 35.472 ms（约 34.5%），区间 [31.832, 39.112] ms。
   不搬页等待对照整步差异 −0.901 ms，区间 [−6.769, 4.967] ms，未检出稳定影响。
5. **搬页主要发生在前向窗口。** 旧版、立即向量化版的系统调用墙钟平均全部在
   forward 窗口；延迟版平均 48.787 ms 与 forward 重叠、0.209 ms 与 backward
   重叠。重叠不是独立因果开销，但明确了后续应重点观察的计算阶段。

### 初步结论与下一步

在本实验设置中，仅改变系统调用启动偏移不足以解释旧版与向量化版的墙钟差异。
即便 ready 后经过的时间相近，前台训练的计算进度、GIL/CPU 调度、缓存和资源竞争
仍可能不同；sleep 不复现 Python 参数构造的实际工作，因此本轮没有唯一确定根因。

下一步优先做**独立搬页与训练并发搬页对照**，记录迁移线程 CPU 时间与墙钟、
上下文切换及细分前向计算窗口。先用线程 CPU 时间与墙钟差区分执行/非执行时间，
但差值不能直接命名为 I/O 等待；必要时再加入 PMU，避免在主要计时运行中使用
重型 profiler。保留相同目标、迁移字节和硬件绑定，不把同步等待移出整步时间。

暂不继续扫描更多固定延迟来挑选最快数值，也不把本轮负结果当作 AOL 无效或
研究假设全部失败的证据。仍需形成可信执行器和工作负载收益空间后，再评估 AOL。

轻量版本管理证据见 [本轮归档](evidence/migration-timing-0929/README.md)。


## 复现命令

从 SoarAlto 根目录执行；输出目录必须不存在：

    python3 research/cpu_training/test_migration_timing.py
    python3 research/cpu_training/run_migration_ablation.py results/timing-diagnostic-new --timing-control --diagnostic
    python3 research/cpu_training/run_migration_ablation.py results/timing-control-new --timing-control --steps 100 --repeats 3
    python3 research/cpu_training/analyze_migration_timing.py results/timing-control-new

本轮结果目录为 results/migration-timing-diagnostic-0929/ 和
results/migration-timing-control-0929/。steps.json 包含每步 task_timeline，
manifest.json、commands.json、protocol.json、runner.py 记录执行版本和预设参数。
分析器使用 _ms 时间单位，仅接受三次重复；校验源码、loss、对象选择、校准调度、
迁移字节、时间戳顺序、请求和实际延迟，以及阶段之和等于整步。

## 解释边界

本轮不采集新的 PMU/PEBS。无论结果如何，都不能把系统调用墙钟解释成纯 CXL 传输，
也不能直接证明 AOL 无效。当前只讨论一个张量、一个训练形状的执行器和时机行为。
所有性能推断在本轮内配对，不能用跨批次绝对耗时变化作为因果证据。

## 调用计时语义补充（2026-09-29）

代码核查确认：syscall_ns 在 Python 中包围 ctypes.CDLL 调用，可能包含返回时
重新获取 GIL 的等待。因此表中“系统调用耗时”应严格理解为 Python 边界的
move_pages 调用墙钟，而非原始内核 syscall 区间。历史字段和数值不改动；
端到端 step 结论保持有效，内部根因尚未确定。内核还存在 RCU/LRU 跨 CPU
协调路径，详见 [原因分析与验证顺序](MIGRATION_CAUSE_ANALYSIS.md)。

## C 内计时与同节点对照（2026-09-29）

完成 18 个训练计时进程和三个独立调用进程。Python 外层额外墙钟均值小于
0.04 ms，向量化跨节点 C 内 CPU/墙钟约 99.6%，不支持 GIL 返回等待或长时间
睡眠为主要原因。同节点调用约 4.913 ms，跨节点约 50.016 ms。35 项回归通过。
随后已完成 sudo 内核热点采样，线程聚合问题已修复，结果见下一节。详见 [本轮确定结论与命令](NATIVE_MIGRATION_REPORT.md)。

## 2026-09-29：内核采样完成，定位 LRU 自旋热点

三组采样完成、丢样为零。修复 perf 默认同名线程聚合造成的 TID 归属遗漏后，
两组跨节点迁移线程的 LRU 路径自旋占内核样本权重 40.70% / 41.63%，
copy_page 为 18.78% / 16.76%；其他线程的匿名缺页/LRU 自旋也明显。
这支持优先检查迁移与前台分配干扰，但不是墙钟分解或因果收益证明。
下一步公平比较缓冲区复用/分配压力，保持初始驻留、迁移量与端到端计时。
详见 [采样修复、确定结论与下一步](KERNEL_MIGRATION_PROFILE_REPORT.md)。
