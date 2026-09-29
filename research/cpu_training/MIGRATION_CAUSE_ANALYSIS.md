# 迁移额外成本的原因分析（2026-09-29）

本文件为代码与已有实验的分析，不代表新计时实验或已确定的性能根因。
现有依据见 [时机对照](MIGRATION_TIMING_REPORT.md)、[向量化对照](VECTORIZED_MIGRATION_REPORT.md)
和 [静态放置对照](PLACEMENT_CAUSE_REPORT.md)。

## 一、需要修正的测量含义

numa_buffer.py 的 syscall_start_ns/end_ns 在 Python 中包围 ctypes.CDLL 调用和 checked。
本机 /usr/lib/python3.10/ctypes/__init__.py 的 CDLL 文档明确：调用期间释放 GIL，返回后
重新获取。因此 syscall_ns 是 Python 边界的 move_pages 调用墙钟，可能包含参数封送、
内核执行/等待、libnuma 包装、调度和返回时重新获取 GIL 的等待。不能等同原始系统调用
进入/退出区间，更不能等同页面复制耗时。现有字段和历史数值保留，解释以本说明为准。

端到端 step 时间仍有效；这项语义修正改变的是内部阶段归因，不是训练快慢的事实。
下一步用小型 C 包装器在外部调用内部读取 CLOCK_MONOTONIC 和 CLOCK_THREAD_CPUTIME_ID，
必要时以单独诊断运行的 syscall tracepoint 交叉检查。线程墙钟与 CPU 时间差不能直接
称作 I/O 等待；CPU 时间也包含线程运行期间的硬件内存停顿和自旋。

## 二、内核中存在比 memcpy 更广的处理

运行内核 build-host-alto/source 指向 kernel/ubuntu-jammy-6.8.0-138/。
该源码 mm/migrate.c 的 do_pages_move 调用 lru_cache_disable，收集/检查页面，再调用
MIGRATE_SYNC 路径迁移，最后 lru_cache_enable。mm/swap.c 的 lru_cache_disable 包含
synchronize_rcu_expedited 和 __lru_add_drain_all(true)。后者扫描在线 CPU，为需要 drain
的 CPU 排队工作并 flush_work 等待；不是所有 CPU 必然都执行任务。

这里的 LRU cache 是内核页管理批处理队列，不是 CPU 的 L1/L2/LLC 数据缓存。
禁用期间 lru_cache_disabled 还会改变 folio_batch_add_and_move 的批处理路径。
因此即使后台线程绑在 CPU8，迁移也可能与前台新页面分配/释放通过页管理路径耦合。
代码证实该机制存在，但没有测出各阶段贡献；不能把约 50 ms 归因给 RCU/LRU。

## 三、当前原型会放大页面管理交互的可能性

access_paths.py 对每个符合条件的 saved tensor 创建 Buffer，不仅创建 ID10。
Buffer 使用新 mmap、MADV_NOHUGEPAGE、mbind、torch.frombuffer 和 tensor.copy_。
因此 forward 不只是算子计算，还包含大量映射、首次写入和副本管理；后台 move_pages
与这些工作重叠。建议分别验证“并发计算”和“并发分配/触页”，不要一开始改为对象池，
以免改变现有实验语义后失去因果对照。

目标 32 MiB 使用 8192 个 4 KiB 页，迁移需要逐页管理、分配目标页和维护映射状态，
不能用字节数除以调用墙钟推断 CXL 硬件带宽。应先测固定调用成本与随页数增长的部分。

## 四、其他合理解释与证据强度

- 计算/迁移共享 socket0 资源：训练绑定 0–7、后台绑定 8；CPU0 与 8 是不同物理核，
  不是彼此 SMT sibling（分别为 0/32 和 8/40）。但 CPU 绑定不隔离共享缓存或内存通路。
  带宽/缓存竞争是候选原因，没有本轮 PMU 证据证明其饱和或占主要比例。
- 同启动时刻不等于同计算进度：Python 参数构造和 sleep 的前台可执行机会不同。
  延迟组对齐约 6.4 ms 后仍比旧版慢约 4.2 ms，只削弱单一时间偏移解释，不排除并发干扰。
- CXL 页面位置不等于每次读都访问 CXL 介质：原型刚把副本写入 CXL，目标缓存驻留可能
  影响结果。总工作集超过 LLC 不足以证明每个目标在 backward 前已冷却。
- 静态 DRAM 对照尚无稳定优势，收益上限本来可能不大；当前没有整进程 DRAM 容量压力，
  也没有测得卸载带来的容量收益。不能默认预取回 DRAM 就一定有可观的收益。
- hooks/unpack 次数不是实际内存访问次数，不能据此断言张量高/低复用或 AOL 高/低。
- n=3 的进程重复不足以支持约 1–3 ms 的稳定改善，频率/后台负载等也是小差异的候选因素。
  当前三轮延迟版相对 Direct 的明显变慢更稳健，但它也不推广到所有张量/模型。

## 五、当前优先验证顺序

1. 校准 Python 边界、C 包装器内和原始 syscall 进入/退出时间，避免把 GIL 返回等待归入内核。
2. 同一目标比较同节点 move_pages（验证未跨节点搬页）、独立跨节点迁移、并发迁移；
   同节点对照仍可能走 LRU/页遍历路径，不能当成零成本函数。
3. 在单独诊断运行中定位 lru_cache_disable、RCU、drain、页面复制和映射更新的相对贡献。
   不直接关闭同步机制或修改内核；若需要权限再给出具体命令。
4. 分离并发计算与并发 mmap/触页，随后才评估批量/池化/复用优化；所有开销仍计入整步。
5. 明确执行器和静态放置收益后，再评估 hotness、AOL 与净收益；不在全负收益样本上强行调模型。

可能需要的收益模型包含静态放置收益、未被隐藏的执行成本、对计算的干扰及容量代价。
不得把完整后台迁移墙钟与前向变慢直接相加，二者存在重叠和重复计数。

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
