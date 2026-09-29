# C 内计时、同节点与独立迁移对照（2026-09-29）

## 目标与已完成范围

承接 [原因分析](MIGRATION_CAUSE_ANALYSIS.md)，先排查 Python/ctypes 返回计时边界，
再判断未实际搬页的调用路径、独立跨节点迁移和训练并发迁移各有多大成本。
本轮未更改 SOAR/ALTO 算法、内核或 sysctl，也没有实施新的预取策略。

已完成六条件各三次独立训练进程（每进程 5 步预热、100 步正式，共 1800 个步骤），
另有六组三步数值/驻留诊断。独立调用实验包含三个独立进程，每进程六条件，
每条件 2 次预热和 12 次正式调用，共 216 次正式调用。35 项相关回归全部通过。
会话中断后检查确认这些运行已完成，未重复计时、覆盖或拼接部分结果。
内核函数级热点采样随后已由用户执行，报告归属问题已修复；详见末节。

## 测量实现与条件

migration_meter.c 通过传入的函数指针调用同一个 libnuma.move_pages，保持原 pid、
页面数组、目的节点与 flags=2。在 C 内记录 CLOCK_MONOTONIC、线程 CPU 时间、
自愿/非自愿上下文切换和 TID，再与 Python 外层区间对照。该 C 区间包含 libnuma
调用和调用内调度，不等于独立硬件传输或严格 syscall tracepoint 区间。
libnuma 的本机反汇编显示 move_pages 为 syscall 的薄封装；没有额外用户态大循环。

C 内时钟在返回 ctypes、重新获取 Python GIL 之前结束。外层减 C 内的差包含 FFI、
C 包装器时钟/getrusage 采样、返回值检查以及可能的 GIL 获取，不能全称作 GIL 等待。
CPU 时间包含实际运行期间的硬件停顿和自旋，不只表示有效复制指令。

训练六条件：direct、原准备 real、原准备+C计时 real_native、向量化 real_vectorized、
向量化+C计时 real_vectorized_native、同节点 same_vectorized_native。最后一组在 CXL
节点 2 请求移动到节点 2，验证仍在 node2，跨节点迁移字节为零；它仍执行绑定、页面
参数准备、move_pages 和状态检查，不能当作零成本函数或纯 LRU 阶段测量。

训练固定 batch=8、sequence=512、width=128、heads=4、layers=2；仅 ID10 的 32 MiB
保存张量初始在 CXL node2，其他受管候选在 DRAM node0。训练绑定 CPU0–7，迁移 CPU8。
每轮条件顺序随机化，种子 20260929；三次进程均值配对 t 区间，df=2、未做多重比较校正。

独立调用在 CPU0–7 准备 DRAM 源与 CXL buffer，CPU8 单工作线程执行调用，主线程等待，
不运行并发 DNN。PyTorch 准备仍使用 8 线程；1/8/32 MiB 分别比较同节点及 node2→node0。
调用后的驻留和数值检查在 C 测量区间之外。这不是训练反事实的严格配对，形状、缓存、
其他分配及线程池活动均可能不同；跨环境差不能直接全部称为 DNN 干扰。

## 训练结果

时间单位为 ms。未使用 C 包装器的条件，其 C 内指标为“不适用”。

| 条件 | 整步训练 | Python 调用墙钟 | C 内调用墙钟 | C 内线程 CPU | 外层额外墙钟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct | 102.374 | 0.000 | 不适用 | 不适用 | 不适用 |
| real | 140.760 | 44.661 | 不适用 | 不适用 | 不适用 |
| real_native | 141.400 | 44.908 | 44.876 | 44.291 | 0.032 |
| real_vectorized | 137.954 | 49.826 | 不适用 | 不适用 | 不适用 |
| real_vectorized_native | 137.150 | 50.054 | 50.016 | 49.825 | 0.038 |
| same_vectorized_native | 109.659 | 4.935 | 4.913 | 4.762 | 0.023 |

### 可以支持的结论

1. **返回 Python/GIL 等待不是本轮几十毫秒成本的主要来源。** 三个 C 计时条件的外层
   额外墙钟均值为 0.032、0.038、0.023 ms；900 次正式 native 调用中的最大外层差约
   0.148 ms。这个上界还包含其他封装开销，因此不能把约 50 ms 或约 5 ms 的主要差异
   解释成返回 Python 时等待 GIL。结论限于本轮样本，不推广到所有 Python 负载。
2. **向量化跨节点调用主要处于线程运行状态。** C 内墙钟 50.016 ms、CPU 时间
   49.825 ms，CPU/墙钟约 99.6%，差约 0.191 ms。因此长时间被调度挂起或睡眠不是
   主要解释。CPU 时间包括内存停顿、自旋和页面管理，尚未确定哪部分占主导。
3. **此前的调用变长在 C 内仍能观察到。** 向量化+C计时相对原准备+C计时，C 内
   墙钟增加 5.139 ms，95% 区间 [3.681, 6.598] ms；CPU 时间增加 5.534 ms，
   区间 [4.277, 6.791] ms。这不是只改变 Python 结束计时位置产生的假象。
4. **不发生跨节点搬运，调用本身也有成本。** 同节点 C 内调用约 4.913 ms，整步
   比 Direct 慢 7.285 ms，区间 [2.100, 12.471] ms。包含调用、页遍历/页管理、绑定、
   参数与状态处理及其干扰，不能把 7.285 ms 全归于 RCU/LRU。
5. **同节点路径不能重现跨节点的全部额外成本。** 跨节点相对同节点的 C 内墙钟
   增加 45.103 ms，区间 [44.275, 45.931] ms，CPU 时间也增加约 45.062 ms。
   二者的系统状态、页面隔离/复制/映射更新不同；该差值不是纯 memcpy 时间，也不能
   据此完全排除延长的跨节点路径对 LRU、锁和前台分配的进一步影响。
6. **本轮仍未得到胜过 Direct 的预取。** 向量化+C计时整步比 Direct 慢 34.776 ms，
   区间 [27.240, 42.311] ms，所有正式步 late_unpacks=0。

本轮 native 两种准备方式之间出现 −4.251 ms 的整步差异，区间 [−4.592, −3.909] ms，
但未经包装的向量化与旧版在此前批次并无稳定收益。新增包装器相对对应旧路径的整步
差异区间跨零（原准备 +0.640 ms；向量化 −0.804 ms），这不是严格的等效性证明。
因此不把这组三次重复的小差异升级为跨批次稳定加速结论，更不称其为新预取策略成果。

## 独立调用结果

每个表项先求每进程 12 次调用的均值，再求三个进程均值。单位 ms。

| 大小及动作 | C 内墙钟 | C 内 CPU | Python 外层额外墙钟 |
| --- | ---: | ---: | ---: |
| 1MiB-same_vectorized_native | 0.369 | 0.087 | 0.007 |
| 1MiB-real_vectorized_native | 0.622 | 0.599 | 0.009 |
| 8MiB-same_vectorized_native | 0.268 | 0.249 | 0.003 |
| 8MiB-real_vectorized_native | 3.412 | 3.326 | 0.007 |
| 32MiB-same_vectorized_native | 1.080 | 0.911 | 0.005 |
| 32MiB-real_vectorized_native | 16.485 | 16.091 | 0.015 |

32 MiB 独立跨节点约 16.485 ms，同节点约 1.080 ms；调用开销随对象规模明显变化，
不是一个对所有大小均固定的 50 ms 延迟。训练并发跨节点约 50.016 ms，支持并发执行
环境放大成本的解释；但两个实验并非完全相同的训练反事实，不能把相差的约 33.5 ms
全部归于带宽竞争。需要核对调用内 CPU 热点，进一步区分复制、锁/自旋、TLB/映射维护、
页管理与前台分配活动。独立 1 MiB 同节点均值高于 8 MiB，说明小调用还存在调度波动，
不能拟合为严格线性带宽曲线。

## 正确性与复现

六个诊断进程 loss/梯度/更新后参数最大绝对误差为零，映射释放检查通过；ID10 初始
8192 页位于 node2，仅 real 系列迁回 node0，同节点控制保持 node2。18 个计时进程
执行源码与二进制哈希、完整 loss 和对象选择一致，迁移量、任务数、调度校准及
内外层计时等式检查通过。独立调用逐次验证数值和页面位置。

从 SoarAlto 根目录执行，所有结果目录必须不存在：

    python3 research/cpu_training/build_native_meter.py
    python3 research/cpu_training/test_native_meter.py
    python3 research/cpu_training/run_migration_ablation.py results/native-diagnostic-new --native-control --diagnostic
    python3 research/cpu_training/run_migration_ablation.py results/native-timing-new --native-control --steps 100 --repeats 3
    python3 research/cpu_training/analyze_native_migration.py training results/native-timing-new
    python3 research/cpu_training/run_native_isolation_matrix.py results/native-isolation-new
    python3 research/cpu_training/analyze_native_migration.py isolation results/native-isolation-new

独立调用矩阵包装器在三个进程均完成后才写 completed.json；本轮原始矩阵由等价的
顺序命令启动。新增包装器用于后续复现，不声称它是本轮已执行的原始启动脚本。
编译器和二进制信息保存在本轮 meter-build.json；新增构建脚本会更新真实编译信息。
本轮归档前未重新编译，不改变已执行版本。不要在正在计时的矩阵中途重建该库。

服务器结果：results/migration-native-diagnostic-0929/、results/migration-native-timing-0929/、
results/migration-native-isolation-0929/。执行源码、C 库、manifest、原始步骤和日志保留。
轻量证据见 [本轮归档](evidence/migration-native-0929/README.md)。

## 后续已完成：内核 CPU 热点定位

用户已执行 run/profile_native_migration_kernel.sh，三组采样完成且无记录丢样。
原 worker 报告存在默认同名线程聚合与 TID 过滤的归属问题，已从原始 perf.data
重新分析，保留全部原报告；新报告的样本数与 period 权重均独立交叉核验通过。
跨节点 worker 的最大单项热点为 LRU 路径自旋（40.70% / 41.63%），
copy_page 为 18.78% / 16.76%。其他线程在匿名缺页/LRU 路径同样出现大量自旋。
这是 CPU 样本证据，不是函数墙钟分解，更不是已经量化的整步减速归因。
下一步优先公平干预前台分配/缓冲复用，先验证机制再推进 AOL。

详见 [内核热点报告](KERNEL_MIGRATION_PROFILE_REPORT.md)。
原始数据位于 results/migration-native-kernel-profile-0929/，
修正分析位于其 verified-analysis/。无需重新运行 sudo 来修复本次报告。
内核符号文件仍仅保留本机，不公开提交。
