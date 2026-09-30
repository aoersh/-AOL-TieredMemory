# 缓冲复用后的内核机制核验（2026-09-30）

## 结论

完成 fresh/reuse_dram × Direct/Prefetch 四条件各三次独立进程的新采样，
每进程 5 步预热、100 步正式。新证据支持：**非目标副本复用使迁移路径和
前台匿名缺页路径上的 LRU 自旋采样权重同时明显降低**。

Prefetch 迁移线程的每步 LRU 自旋权重从 66.051 降到 33.094 百万采样周期，
下降约 49.90%；其余线程的匿名缺页/LRU 自旋从 551.995 降到 281.901。
迁移线程的 copy_page 权重从 30.664 到 29.408，差值区间跨零，未观察到
与自旋同等幅度的复制权重下降。与此同时，前台 PTE 页表锁自旋也大幅减少。

这比“热点存在”更进一步：复用干预、无采样性能改善、同配置新采样中的
锁路径权重下降三者相互吻合。但还不能把此前 9.513 ms/步的额外预取成本
改善全部归为 LRU，也没有测量锁地址/持有者，不能给出各锁的独立墙钟贡献。

预取收益结论继续使用[无采样实验](BUFFER_REUSE_REPORT.md)：复用 Direct
为 88.560 ms，复用 Prefetch 为 107.460 ms，后者仍慢 18.900 ms。
本次采样不替代性能结果；尚无预取赢家，不进入复杂 ML，不否定 AOL。

## 条件与完整性

继续使用同一个 Transformer 配置、CPU0–7 计算、CPU8 迁移、DRAM node0、
CXL node2。ID10 每步仍在 node2 新建，Prefetch 每步迁移 32 MiB；
reuse_dram 只复用非目标 node0 的 102.15625 MiB 副本映射，复制保留。
训练源码、C 计时库均未改变，哈希与此前正式诊断/无采样计时一致。

- 新采样顺序按种子 202609301 分三个随机区组，串行启动 12 个独立进程。
- 使用项目 kernel/build-perf/perf，cycles:k、199 Hz、frame-pointer 调用链。
- 显式指定 --clockid mono，与 Python/C 的 CLOCK_MONOTONIC 时间对齐。
- 共记录 219017 个样本，落入正式步骤区间的样本 202249 个；12 组丢样均为 0。
- 原始带调用链解析与独立无调用链导出的每 TID 样本数、period 总和相等；
  再与 perf report 的总事件权重交叉核对。
- 1200 个正式步骤的 loss、对象选择、源码与二进制哈希、迁移量、调度校准
  和计时等式全部核验；600 个正式预取步骤均迁移 32 MiB，late_unpacks=0。
- 沿用同源码的四组数值/驻留诊断；本次没有把梯度复制或逐页查询插入采样运行。
- 新增六项时间/地址边界/锁分类测试，加原解析器三项，共九项通过。

## 无需新 sudo 的符号核验

普通用户可记录 cycles:k，但 kptr_restrict=1 导致 perf 缺少内核映射/重定位
信息，即使传 --kallsyms，自动报告仍输出 unknown。这次没有更改权限/sysctl。
sudo -n 在沙箱外也需要密码，因此先验证已有只读快照能否可靠解析本次原始 IP。

1. 当前与 09-29 原始数据的内核 build-ID 均为
   6094298925f6356274bc9ca6a20deabb6b33b1e4。
2. 用 wall clock − monotonic 估算本次启动时刻，再用旧记录文件时间 − 旧末步
   monotonic 时刻估算启动时刻，探针差约 0.350 秒，包含旧 perf 写文件尾部时间。
   运行入口要求差值不超过 5 秒，否则拒绝沿用旧地址。整个矩阵 boot_id 不变。
3. 使用旧快照中的绝对地址，只解析核心内核 [_stext,_etext) 范围内文本符号，
   不猜测模块或范围外地址。以旧 perf 成功解析的符号作为 oracle，
   **196050 个核心内核栈帧全部匹配，零不一致**，包含同地址符号别名的处理。
4. 再用一个新小探针验证原始 IP 解析、总权重与无丢样检查，然后启动正式矩阵。

快照哈希、build-ID、启动信息和执行脚本均已保存。该方案只适用于核验通过的
同次启动；重启/换内核后必须获得新的符号快照，不能用本报告当作长期授权
沿用旧地址。只解析核心内核，模块路径不做归因。正式窗口中每进程 1.84%–3.34%
权重在核心范围外（可能包括用户态 skid、模块或未解析 IP），全部保留在分母中。
这些范围外权重不能统一称作“内核解析失败”。本次 worker 范围外权重为 0。

没有重跑 09-29 的训练采样；只读其原始 perf.data 做符号交叉校验。
自动 perf report 的 unknown 警告保存备查，科学统计使用已验证的原始 IP 解析器。

## 计量口径

采样记录包含完整进程生命周期，分析按每一个正式 step 的 [start_ns,end_ns)
半开区间筛选，排除预热、启动、日志间隙和最终关闭。进一步按 C 内
[native_start_ns,native_end_ns) 划分迁移调用期间与其他时段。

下文 M/step = 该组 period 权重 / 100 / 10^6，再对三进程取均值。它是
**每步百万采样周期权重估计**，不是函数墙钟，也不是无误差硬件精确计数。
period 代表的采样区间可能跨越阶段边界，按采样时刻归属会有边缘偏差。
百分比对每进程先算再取均值；分母为明确标注的线程/时间组全部权重。

LRU 分类要求自身 IP 为 native_queued_spin_lock_slowpath，且调用链包含
folio_lruvec_lock_irqsave / folio_batch_move_lru / folio_add_lru 等路径；
匿名缺页/LRU 再要求包含 do_anonymous_page。它们属于嵌套类别，不能相加。
PTE 分类要求自旋调用链包含 __pte_offset_map_lock，不能并入 LRU。
copy_page 也按自身采样 IP 计权，不是含子调用的累计热点。

Direct 没有 native 迁移事件，因此不识别其保留的空闲 worker TID；
Direct 的“其余线程”包含全部线程，不能把统计中的空 worker 组解释为
已经证明 worker 完全没有内核活动。Prefetch 的 worker 由 C 内记录 TID 精确识别。

## 核心结果

| 正式步骤中的指标 | fresh Prefetch | reuse Prefetch |
| --- | ---: | ---: |
| 迁移线程全部权重，M/step | 165.339 | 118.785 |
| 迁移线程 LRU 自旋，M/step | 66.051 | 33.094 |
| 迁移线程 LRU 自旋，占该线程权重 | 39.94% | 27.87% |
| 迁移线程 copy_page，M/step | 30.664 | 29.408 |
| 迁移线程 copy_page，占该线程权重 | 18.54% | 24.77% |
| 其余线程匿名缺页/LRU 自旋，M/step | 551.995 | 281.901 |
| 其余线程上述自旋落在 native 调用期间的比例 | 98.85% | 98.23% |
| 其余线程 PTE 页表锁自旋，M/step | 138.399 | 2.074 |

迁移线程正式窗口样本数：fresh 三轮为 1004/1023/1030，reuse 为 766/755/747。
其中 C 内窗口为 1004/1022/1030 和 766/755/746。所有已分类 worker LRU 自旋
样本都在 native 调用区间，因此按整个正式 step 或 C 内窗口算其绝对权重相同。

三轮配对差（fresh Prefetch − reuse Prefetch，单位 M/step；
t(df=2) 95% 区间，未做多重比较校正）：

| 指标 | 减少量 | 95% 区间 |
| --- | ---: | --- |
| worker LRU 自旋 | 32.957 | [23.033,42.882] |
| worker copy_page | 1.255 | [-0.958,3.468] |
| 其余线程匿名缺页/LRU 自旋 | 270.093 | [199.649,340.537] |

复制的占比升高，主要因为该线程其他权重下降；不能据此声称复制变慢。
复制差值区间跨零也不是严格等效性证明。前台 native 期间的匿名缺页/LRU 自旋
占该时间组权重从约 51.10% 到 54.55%，但其每步绝对权重从 545.655 降到
276.920；再次说明只看比例会误判干预方向。

Direct 对照补充：全部线程 LRU 自旋为 fresh 6.043、reuse 5.221 M/step，
减少 0.822 的区间 [-0.990,2.634] 跨零，明显低于 Prefetch 的前台 LRU 权重。
但 Direct 的 PTE 自旋也从 48.849 降到 0.536 M/step，因此复用对 Direct 的
改善同样包含页表/分配管理变化，不能把全部改善都归于迁移特有机制。

### 采样运行的状态核对

以下仅用于确认新进程执行状态，不替代无采样性能：

| 条件 | 整步 ms | C 内迁移墙钟 ms | C 内线程 CPU ms | minor faults/步 |
| --- | ---: | ---: | ---: | ---: |
| fresh Direct | 101.810 | 不适用 | 不适用 | 129161.4 |
| fresh Prefetch | 138.309 | 50.710 | 50.514 | 134029.2 |
| reuse Direct | 90.088 | 不适用 | 不适用 | 109971.3 |
| reuse Prefetch | 110.994 | 37.279 | 37.135 | 110130.0 |

fresh 与 reuse Prefetch 的 Python 外层额外调用时间仍仅约 0.043/0.037 ms，
没有出现以 GIL 返回等待为主的反向证据。

## 证据边界与下一步

现在可以说，分配复用干预后，前台与迁移的 LRU 自旋**采样权重确实下降**，
且前台 LRU 自旋高度集中在迁移调用期间；这一机制解释与无采样的性能改善一致。
但复用同时改变 PTE 锁、mmap/munmap、首次缺页、地址/缓存状态和保留内存，
仍没有单独控制每个因素。不能把采样周期换算为 9.513 ms 的因果分解，
也不能把前台与 worker 周期相加后当成整步墙钟。

下一步优先固定迁移目标做独立 2×2 对照：前台新映射/首次写入 vs 已触页复用，
分别配合迁移 vs 不迁移。保持前台写入字节、CPU0–7、worker CPU8、目标
32 MiB 和逐轮初始 node2 驻留一致；记录 native、前台、实际重叠、缺页及
完整试验时间，初始化/目标重置/释放均记账。先正确性/驻留，再随机三进程。
该实验用于检验并发分配是否足以复现放大效应；没有 DNN，不能当作网络加速。
若 PTE 与 LRU 仍耦合，再用前台线程数或预触页时机做少量后续拆分，不直接改内核。

## 复现与归档

以下为同次启动时的复现命令，输出目录必须不存在；符号前置检查失败时停止，
不要放宽阈值以强行使用旧地址：

```bash
python3 research/cpu_training/test_buffer_reuse_kernel.py
python3 research/cpu_training/test_kernel_profile.py
python3 research/cpu_training/profile_buffer_reuse.py results/buffer-reuse-kernel-new
python3 research/cpu_training/analyze_buffer_reuse_kernel.py results/buffer-reuse-kernel-new \
  --output results/buffer-reuse-kernel-new/verified-analysis
```

本轮原始数据：results/buffer-reuse-kernel-profile-0930-v1/。
有效分析：**verified-analysis-v2/**，增加了 PTE/分配器自旋分类；
初版 verified-analysis/ 保留，二者来自同一批数据，没有重复训练采样。
探针与符号前置核验：results/buffer-reuse-kernel-probe-0930-v1/。
轻量归档：[evidence/buffer-reuse-kernel-0930](evidence/buffer-reuse-kernel-0930/README.md)。
原始 perf.data、kallsyms 和包含地址的自动报告只留本机，不纳入轻量证据。
