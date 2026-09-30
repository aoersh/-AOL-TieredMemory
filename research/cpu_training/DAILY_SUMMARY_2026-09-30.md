# CPU DNN + CXL 研究日总结（2026-09-30）

## 今天完成的工作

今天围绕“迁移与前台分配是否相互干扰”完成了两层验证：先做非目标 DRAM
缓冲复用的端到端干预，再对干预前后做内核 cycles:k 采样。

实验固定为 CPU eager FP32 Transformer：batch8、sequence512、width128、heads4、
layers2、SGD；计算 CPU0–7，迁移线程 CPU8；DRAM node0，真实 CXL node2。
只有 saved tensor ID10（32 MiB、8192 个 4 KiB 页）每步初始位于 CXL，
没有全进程 DRAM 容量限制。

新增 `reuse_dram` 缓冲池，只复用非目标 node0 saved-tensor 副本，容量
102.15625 MiB；每步仍复制当前内容。ID10 仍每步新建，Prefetch 仍迁移
32 MiB。初始化、首次触页、每步 reset、lease 检查和关闭都保留在账目中。

## 端到端结果

四条件各三次独立进程，每进程 5 步预热、100 步正式，顺序随机化：

| 条件 | Direct | Prefetch |
| --- | ---: | ---: |
| 每步新分配 | 104.743 ms/步 | 133.156 ms/步 |
| 复用非目标 DRAM 副本 | 88.560 ms/步 | 107.460 ms/步 |

复用将 Prefetch 相对 Direct 的额外成本从 28.413 降至 18.900 ms/步。
差中差为 **9.513 ms/步**，95% 配对 t 区间为 **[4.581, 14.445] ms**。
复用 Prefetch 仍比复用 Direct 慢 **18.900 ms/步**，尚未发现预取赢家。

四组三步诊断中 loss、全部梯度、更新后参数最大绝对误差均为 0；逐页驻留、
storage 别名、重复解包、原地修改拒绝、异步生命周期和释放检查全部通过。
共 32 项回归通过，分析器对六类故意损坏记录均正确拒绝。

## 内核机制结果

fresh/reuse × Direct/Prefetch 各三次新采样，每次 5 步预热、100 步正式。
共记录 219017 个样本，其中 202249 个落入正式 step 区间，12 组丢样为零。
按 step 半开区间和 C 内 native 区间裁切，并独立核对 TID、period 和 perf report
事件权重。

Prefetch 迁移线程的每步 LRU 自旋采样权重从 **66.051** 降至 **33.094** 百万
周期，约下降 49.90%；其他线程匿名缺页/LRU 自旋从 **551.995** 降至
**281.901**。前台该路径约 98% 的权重发生在 native 迁移期间。

迁移线程 `copy_page` 从 30.664 降至 29.408，差值区间跨零；因此没有看到
与 LRU 自旋同等幅度的复制权重下降。PTE 页表锁自旋也显著下降，说明复用
同时改变了缺页、页表和分配路径，不能把 9.513 ms 全部归因于 LRU 自旋。

普通用户记录的 perf 自动符号解析受 kptr_restrict 限制。当前内核与旧数据
build-ID 一致，启动时刻一致性检查通过；旧数据中的 196050 个核心内核栈帧
全部通过符号 oracle 交叉核验。只解析核心内核 text，模块和范围外地址保留
在分母中，不作错误归因。
时间区间、线程权重、符号边界和锁分类等九项解析回归全部通过。

## 代码与证据

- 缓冲池实现：`access_paths.py`、`numa_buffer.py`、`run_access_paths.py`。
- 端到端实验：`run_buffer_reuse.py`、`analyze_buffer_reuse.py`、`test_buffer_reuse.py`。
- 内核采样：`profile_buffer_reuse.py`、`analyze_buffer_reuse_kernel.py`、
  `test_buffer_reuse_kernel.py`。
- 端到端报告：[BUFFER_REUSE_REPORT.md](BUFFER_REUSE_REPORT.md)。
- 机制报告：[BUFFER_REUSE_KERNEL_REPORT.md](BUFFER_REUSE_KERNEL_REPORT.md)。
- 轻量证据：`evidence/buffer-reuse-0930/` 和 `evidence/buffer-reuse-kernel-0930/`。
- 原始本机数据仍保存在 `results/`，未加入 Git；内核 `perf.data`、`kallsyms`
  和包含地址的报告未进入轻量证据。

## 下一步计划

下一步做独立的 2×2 分配压力对照：前台“新映射/首次写入”与“已触页复用”，
分别配合 32 MiB 迁移与不迁移。固定 CPU、写入字节、迁移目标、初始 CXL 驻留，
记录 native 时间、前台阶段、实际重叠、缺页、驻留和完整生命周期成本。

先完成正确性和驻留验证，再随机顺序进行至少三次独立进程测量。若 PTE 与 LRU
仍然耦合，再用少量前台线程数或预触页时机对照拆分；不修改内核、不删除
`lru_cache_disable`。在出现可重复的净收益边界以前，不引入复杂 ML，也不提前
把 AOL 与预取收益绑定。
