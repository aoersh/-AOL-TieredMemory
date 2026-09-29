# 迁移线程内核热点核验（2026-09-29）

## 结论

用户执行的三组 sudo 采样均已完成，原始 perf.data 可用。纠正线程报告的聚合问题后，
两种跨节点迁移的最大单项热点均为 **LRU 页面链表路径上的自旋锁竞争**，不是页面复制。
其他线程同时在匿名缺页后的 LRU 入队路径大量自旋。这为“并发迁移与前台页面分配
相互干扰”提供了直接调用栈证据，应优先排查这一机制。

这不是已证明唯一根因或已经修复训练性能：未测得锁地址/持有者，未用干预实验量化
该机制对整步减速的因果贡献。尤其不能把样本占比直接乘以训练时间，声称可获得同等加速。

## 完整性与报告修复

三个进程各 5 步预热、100 步正式；完整 loss 序列、对象选择、执行源码及 C 库哈希
一致，已核对归档文件的实际哈希。正式步骤均按预期执行一次任务，跨节点每步
32 MiB，同节点实际跨节点迁移量为 0；全部 late_unpacks=0。此次为热点诊断，
未额外执行逐步梯度/参数诊断；此前数值/驻留诊断的范围见原生计时报告。

| 条件 | 全部样本 | 迁移线程样本 | 丢样 | C 内调用均值 ms | 整步均值 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 同节点 same_vectorized_native | 16620 | 167 | 0 | 5.397 | 101.289 |
| 向量化跨节点 real_vectorized_native | 23732 | 1066 | 0 | 50.983 | 139.763 |
| 原准备跨节点 real_native | 23459 | 1050 | 0 | 45.924 | 140.088 |

均值仅用于核对运行状态：每条件只有一个进程，且开启 profiler，不替代此前三轮
无采样性能实验。此次没有 Direct 的配对内核 profile，不能从本表推断同节点与 Direct 等价。

原始 worker-report.txt 接近空白，并非 worker 未被采到。项目 perf 的默认
comm,dso,symbol 聚合将同名 python3 线程的同一符号合并，随后 TID 筛选不能保持
迁移线程的完整归属。仅降低阈值、改相对百分比也不够：三个 worker 分别只剩
119/408/148 个样本，而原始数据实际有 167/1066/1050 个。该现象在这份本机 perf
构建及此数据上已复验，不概括为所有 perf 版本均有此问题。

修正为：

```bash
kernel/build-perf/perf report --stdio --no-children --percent-limit 0 \
  --sort comm,pid,dso,symbol --percentage relative \
  --tid <worker-tid> --kallsyms <结果目录>/kallsyms.txt \
  -i <结果目录>/<条件>.perf.data
```

该版本的 sort pid 实际比较 thread__tid（tools/perf/util/sort.c 的 sort__thread_cmp）。
现已把这个维度显式加入采样脚本；新增 analyze_kernel_profile.py 从 perf script
逐条按 TID 汇总，再与独立无调用栈导出和修正后的 perf report 交叉核对。
三组样本数及 period 总和守恒，worker 热点百分比与 report 四舍五入结果一致。
原报告原样保留；应使用 verified-analysis/ 中的新报告。无需重新 sudo 采样。

## 热点证据

以下占比是 **整个迁移线程生命周期内 cycles:k 样本的 period 加权占比**，
包含预热及其他内核活动，不是仅正式 move_pages 调用的时间分解。

| worker 自身采样 IP | 向量化跨节点 | 原准备跨节点 |
| --- | ---: | ---: |
| native_queued_spin_lock_slowpath | 40.70% | 41.63% |
| copy_page | 18.78% | 16.76% |
| migrate_pages_batch | 13.19% | 14.46% |

两组迁移线程的所有已采到的 slowpath 自旋样本均走以下调用路径（从调用者到热点）：

```text
move_pages → migrate_pages → migrate_pages_batch
  → folio_add_lru → folio_batch_move_lru
  → folio_lruvec_lock_irqsave → native_queued_spin_lock_slowpath
```

其他线程中，以下匿名缺页/LRU 路径的自旋占其各自内核采样权重约 28.05% 和 27.82%；
同节点对照约 1.61%。三个分母均为对应进程中除迁移线程外的全部采样权重，
包括启动和预热；它们不是正式阶段的前向墙钟占比。

```text
do_anonymous_page → folio_add_lru_vma → folio_add_lru
  → folio_batch_move_lru → folio_lruvec_lock_irqsave
  → native_queued_spin_lock_slowpath
```

这是迁移路径和匿名分配路径都在 LRU 锁上争用的证据；尚不能凭调用栈证明它们
等待的具体锁地址相同，也没有排除其他进程/内核线程的影响。
同节点 worker 仅有 167 个样本，主要是页查找、读锁与用户数组访问；未采到该
自旋热点不等于绝对没有自旋。页面复制有显著成本，但当前不支持“只是 CXL 复制慢”
或“已经证明带宽饱和”的解释。未专门测量内存控制器带宽。

## 与当前内核代码和训练实现的关系

本机内核源码 mm/migrate.c 的 do_pages_move 在收集/迁移页面前调用
lru_cache_disable，结束时恢复。mm/swap.c 的 lru_cache_disable 更新全局
lru_disable_count，执行 RCU 同步和 drain；folio_batch_add_and_move 在
lru_cache_disabled 时直接进入 folio_batch_move_lru，无法走通常的暂存返回路径。
后者会获取 lruvec 锁。这里的 LRU cache 是内核页面暂存机制，不是 CPU LLC。

结合栈证据，合理机制是：跨节点调用持续期间，前台并发匿名缺页和迁移页重新加入
LRU 都频繁进入持锁路径，放大迁移及前向开销。该机制与此前 C 内高 CPU 占比相容，
不要求长时间睡眠。**全局禁用/批处理行为是源码事实，其具体性能贡献仍是待干预验证的解释。**

当前保存张量实现会对所有受管候选新建 mmap/mbind/copy，并不只处理 ID10，
因此可能放大匿名缺页与迁移的重叠。这可能属于当前实验实现的放大效应，
尚不能归为所有 CPU DNN、SoarAlto 或 TierTrain 的固有缺陷。

## 下一步最小干预

1. 优先增加“缓冲区预分配并复用”对照，减少正式前向中新 mmap/首次缺页。
   Direct 与 Prefetch 同时应用；每步前保证 ID10 初始在 CXL，迁移仍是 32 MiB。
   记录并计入所有新增重置/迁回开销，单列一次性初始化，不能通过移出计时制造收益。
   若生命周期、别名或节点重置无法公平保持，先做独立分配压力对照，不直接声称训练加速。
2. 正确性、页面驻留及任务生命周期检查通过后，各条件至少 3 次独立进程，
   随机顺序；记录整步、前向、native 墙钟/CPU、缺页和迁移量。必要时单独再次采样。
3. 如果减少分配确实同时降低 LRU 自旋和额外训练成本，再测张量净收益与 AOL；
   如果没有，继续查页表/TLB、复制及分配器，保留这次否定结果。

本次没有更改内核同步机制、关闭全局 LRU/MGLRU、重启或修改 AOL。
不应为消除锁热点直接删除 lru_cache_disable。

## 文件与复现

- 原始采样：results/migration-native-kernel-profile-0929/，保留三个 perf.data、
  用户实际执行的 runner.sh、训练步骤及原报告。
- 有效分析：同目录 verified-analysis/summary.json、三个 worker-report.txt
  及 worker-callgraph.txt；completed.json 表示分析校验通过。
- 轻量归档：[evidence/migration-kernel-profile-0929](evidence/migration-kernel-profile-0929/README.md)。
- 对同一原始数据重新分析（新输出目录必须不存在）：

```bash
python3 research/cpu_training/analyze_kernel_profile.py \
  results/migration-native-kernel-profile-0929 \
  --output results/migration-native-kernel-profile-0929/analysis-new
python3 research/cpu_training/test_kernel_profile.py
```

kallsyms.txt 和原始 perf.data 仅留本机，不纳入公开证据；轻量归档仅包含符号统计、
去地址后的调用链、manifest、哈希及分析源码。
