# 页面参数向量化对照（2026-09-29）

## 问题与改动

前轮 [迁移成本拆分](MIGRATION_ABLATION_REPORT.md) 观察到逐页 Python/ctypes 参数
构造存在额外开销。本轮只增加可选的 NumPy 批量构造路径，保持原迁移函数、调度、
绑定策略、系统调用参数、返回状态检查和训练模型不变，不修改 SOAR/ALTO 核心算法。

vectorized_arguments 每次根据当前 buffer 地址生成 np.uintp 页面地址数组和 np.intc
目标节点数组；通过 ctypes.from_buffer 提供原有 ABI 的连续参数。每次任务重新分配，
不缓存页地址。底层 NumPy 数组由 ctypes 引用保持存活。status 仍使用原 ctypes 数组。
构造、调用和局部变量清理均处于训练计时覆盖的任务内，没有提前生成参数来移出成本。
准备阶段计时包含导入查找、分配和初始化；局部变量清理由整个任务/训练步计时覆盖。

新增 prepare_vectorized（只准备、不搬页）及 real_vectorized（真实搬页）两种
显式选项。原 direct/ranked/noop/prepare/real 五条件保留，同一源码同批运行。
默认 ranked 行为不变；本轮是执行器优化验证，不是新的收益感知策略。

## 固定实验条件

- Transformer：batch=8、sequence=512、width=128、heads=4、layers=2；FP32 SGD。
- 仅 ID10 的 32 MiB 保存张量初始位于 CXL node2，其余受管候选在 DRAM node0。
- 训练 CPU 0–7，后台迁移 CPU8；numactl 启动绑定 CPU0–8、内存 node0。
- 七条件，每条件三次独立进程；每进程 5 步预热、100 步正式计时，共 2100 个测量步骤。
- 每轮按种子 20260929 随机排列条件，串行运行；另有七个三步正确性/驻留诊断。
- 三个独立进程均值的配对 t 区间，df=2，未做多重比较校正；不能把训练步骤视为独立重复。
- 保留原有绑定、MPOL_BIND 与系统 NUMA 设置；不增加 DRAM 容量上限，不新增 PMU/PEBS。

## 执行与结果

本轮已完成全部 21 个计时进程、2100 个正式步骤和七个三步诊断。用户反馈会话
异常中断后检查发现 completed.json 与全部步骤文件已经生成，未发现 failure.txt；
故继续分析现有数据，没有重复运行、覆盖结果或将不完整进程拼接进统计。
本记录只能确认实验产物完整，不判断会话中断的具体原因。

时间单位为毫秒；step 是整步训练，其余为后台任务阶段墙钟，不能直接相加。
ranked 未记录 prepare/syscall 子阶段，表中以“未拆分”显示，不能解读为零成本。

| 条件 | 整步训练 | 参数准备 | move_pages 调用 | 迁移任务总计 |
| --- | ---: | ---: | ---: | ---: |
| direct | 105.665 | 0.000 | 0.000 | 0.000 |
| noop | 102.593 | 0.000 | 0.000 | 0.000 |
| prepare | 111.329 | 5.844 | 0.000 | 0.000 |
| prepare_vectorized | 104.574 | 0.106 | 0.000 | 0.000 |
| ranked | 144.759 | 未拆分 | 未拆分 | 52.782 |
| real | 143.365 | 5.776 | 46.241 | 53.232 |
| real_vectorized | 141.307 | 0.215 | 51.764 | 53.150 |

noop/prepare/prepare_vectorized 不实际搬页，其 migration 为 0 是事件分类结果，
不表示准备任务没有耗时；仅真实迁移三组每步搬运 32 MiB。

| 配对差异（前者减后者） | 整步差异 ms | 95% t 区间 |
| --- | ---: | --- |
| prepare_vectorized-prepare | -6.755 | [-16.063, 2.553] |
| real_vectorized-real | -2.058 | [-7.914, 3.797] |
| prepare_vectorized-noop | +1.981 | [-10.486, 14.447] |
| real_vectorized-direct | +35.642 | [30.879, 40.404] |
| real_vectorized-prepare_vectorized | +36.733 | [23.691, 49.775] |
| real-ranked | -1.393 | [-6.882, 4.095] |
| noop-direct | -3.072 | [-19.811, 13.666] |

主要观察：

1. 参数构造自身确实变快：真实迁移的准备从 5.776 降至 0.215 ms，约下降 96.3%；
   配对差异 −5.561 ms，95% 区间 [−5.637, −5.486] ms。只准备组从 5.844 降至
   0.106 ms，配对差异 −5.738 ms，区间 [−5.794, −5.682] ms。
2. 真实迁移整步平均从 143.365 降至 141.307 ms，约 1.4%，但配对区间跨零，
   三轮差异分别为 −3.150、−3.671、+0.647 ms；**尚不能声称稳定训练加速**。
3. 真实迁移的 move_pages 墙钟反而从 46.241 增至 51.764 ms：配对 +5.523 ms，
   区间 [4.760, 6.286] ms。整个迁移任务仅差 −0.081 ms，区间 [−1.135, 0.973] ms。
   因此缩短准备并未同等缩短任务，简单的“准备时间减少多少，训练就快多少”不成立。
4. 向量化只准备组整步比旧准备组平均减少 6.755 ms，但区间 [−16.063, 2.553] ms
   跨零；其前向阶段差异 −5.812 ms，区间 [−10.039, −1.584] ms。不能将阶段改善
   或三个同向点估计替代端到端的可靠改善证据。
5. 优化后的真实迁移仍比 Direct 慢 35.642 ms/step（约 33.7%），区间
   [30.879, 40.404] ms。所有正式步骤 late_unpacks=0；仍未找到稳定预取赢家。

## 当前解释与下一步验证

一个可检验的解释是：准备缩短使搬页更早进入计算密集或内存争用阶段，改变了系统调用
与训练的重叠；也可能存在内核等待、页锁、缓存或调度影响。当前没有调用内的细分事件，
也没有 GIL 或带宽计数，不能确定因果，更不能把增加的 syscall 墙钟称为额外硬件传输。

下一步做**迁移开始时机对照**，保留旧版、向量化版，并增加仅用于归因的向量化延迟启动
对照；预先固定延迟依据和参数，不能事后挑选最快设置。增加目标 ready、参数准备结束、
系统调用开始/结束以及训练阶段时间戳，验证时机变化是否解释 syscall 变长。
延迟也计入任务和端到端训练时间，不把它称作性能策略。先诊断和小矩阵，再决定是否
开展分批搬页、PMU 干扰验证或扩大 tensor 范围。仍不急于改造 AOL/引入机器学习。

## 正确性与回归

七个诊断的 loss/梯度/更新后参数最大绝对误差均为 0，映射释放检查通过。ID10 初始
8192 页在 node2，使用时仅 ranked/real/real_vectorized 在 node0，其他条件保持 node2。
21 个计时进程的执行源码、完整 loss 序列与目标选择一致，逐步迁移量和调度校准检查通过。
向量化新增检查覆盖不搬页、错误传播、部分迁移拒绝、不同尺寸/目标节点的完整地址数组
以及底层数组存活性。30 项相关回归、Python 编译检查及 git diff --check 通过。

轻量证据见 [本轮归档](evidence/migration-vectorized-0929/README.md)，完整步骤保留在
服务器结果目录。summary.json 全部时间键使用 _ms，避免前轮 *_ns 标签但值为毫秒的歧义。


## 复现与代码入口

在 SoarAlto 根目录运行，结果目录必须不存在：

    python3 research/cpu_training/run_migration_ablation.py results/vectorized-diagnostic-new --diagnostic --vectorized
    python3 research/cpu_training/run_migration_ablation.py results/vectorized-timing-new --vectorized --steps 100 --repeats 3
    python3 research/cpu_training/analyze_vectorized_migration.py results/vectorized-timing-new
    python3 research/cpu_training/test_migration_ablation.py

代码入口：numa_buffer.py 的 vectorized_arguments/migration_probe；access_paths.py
任务分派与记账；run_access_paths.py 的 --migration-action；运行矩阵复用
run_migration_ablation.py --vectorized。analyze_vectorized_migration.py 校验同源码、
完整 loss 序列、目标选择、调度校准、迁移字节和计时之和，时间字段明确使用 _ms。
本轮分析器仅接受三次重复；其他重复数需调整统计实现。

原始结果目录：results/migration-vectorized-diagnostic-0929/ 与
results/migration-vectorized-timing-0929/。完整命令、环境、源码快照和日志随运行保存。

## 解释限制

若观察到训练改善，只支持当前执行器实现的优化收益，不证明 GIL 是唯一机制。
若数组准备更快但整步仍慢，需继续研究迁移与计算的相互影响，不能判定 AOL 无效。
real 对 prepare 的差异包含绑定、驻留、缓存与迁移路径；系统调用墙钟不是纯传输时间。
这里是单个张量和单一形状的探索性实验，不等于整个进程 CXL-only，也不是完整 ALTO
训练策略或论文数值复现。不同批次绝对性能不能直接比较，本轮结论使用同批配对。

## 固定延迟时机对照（2026-09-29）

六条件、18 个计时进程完成。向量化后固定等待 5.5 ms，使系统调用启动偏移
接近旧版（6.445 vs 6.358 ms），但调用仍慢约 4.197 ms，区间高于零；整步
未稳定改善。单纯启动时间偏移不足以解释差异。下一步比较独立/并发搬页并
记录线程 CPU 时间与计算阶段，暂不改造 AOL。详见 [时机对照报告](MIGRATION_TIMING_REPORT.md)。

## 调用计时语义补充（2026-09-29）

代码核查确认：syscall_ns 在 Python 中包围 ctypes.CDLL 调用，可能包含返回时
重新获取 GIL 的等待。因此表中“系统调用耗时”应严格理解为 Python 边界的
move_pages 调用墙钟，而非原始内核 syscall 区间。历史字段和数值不改动；
端到端 step 结论保持有效，内部根因尚未确定。内核还存在 RCU/LRU 跨 CPU
协调路径，详见 [原因分析与验证顺序](MIGRATION_CAUSE_ANALYSIS.md)。
