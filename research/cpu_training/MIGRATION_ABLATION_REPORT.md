# 页面迁移成本拆分实验（2026-09-29）

## 目的与设计

承接 [三方放置对照](PLACEMENT_CAUSE_REPORT.md)，解释预取额外成本，再研究 AOL 与净收益的关系。
本轮不修改 SOAR/ALTO 核心算法。大配置 Transformer：batch=8、sequence=512、width=128、
heads=4、layers=2，FP32 SGD，固定合成输入。仅保存张量 ID10（[8,4,512,512]，32 MiB，
8192 个 4 KiB 页）初始置于 CXL 节点 2；其余受管候选在 DRAM 0。这不是整进程 CXL-only。
CPU 训练绑定 0–7，迁移线程绑定 8；进程启动绑定 DRAM 0。

五种条件：

- direct：直接访问 CXL，不提交预取任务。
- ranked：原有历史需求优先级异步迁移。
- noop：相同 ranked 调度和等待；空任务不准备数组、不绑定、不搬页，数据保持 CXL。
- prepare：同样调度，只构造 pages/nodes/status 数组，不调用 mbind/move_pages，保持 CXL。
- real：按原顺序执行绑定、数组准备、move_pages、状态检查，分别计时。

每条件三次独立进程，每进程 5 步预热加 100 步测量，共 15 进程、1500 个正式步骤。
每轮随机排列五条件（种子 20260929），串行运行。另跑五个三步诊断进程。
95% 区间使用三个进程均值的配对差异，t 临界值 4.30265273（df=2）。
不是把步骤当独立重复；区间未经多重比较校正，仅作探索性解释。

内核 6.8.12-138-soaralto；DRAM 节点 0/1、CXL 节点 2/3；numa_balancing=1、
pte_scale=16、perf_event_paranoid=-1。本轮未更改系统参数，未新增 PMU/PEBS 采样。

## 结果

时间单位 ms/step，迁移量 MiB/step。wait 已含在阶段计时中，不可再加到 step。

| 条件 | 整步 | forward | backward | 需求处等待 | 迁移量 |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct | 102.464 | 43.623 | 57.885 | 0.000 | 0 |
| noop | 103.775 | 44.370 | 58.476 | 0.047 | 0 |
| prepare | 110.543 | 50.247 | 59.336 | 0.047 | 0 |
| ranked | 140.995 | 78.333 | 61.509 | 0.045 | 32 |
| real | 140.522 | 76.828 | 62.500 | 0.045 | 32 |

| 配对差异（前者减后者） | 整步差异 ms | 95% t 区间 |
| --- | ---: | --- |
| noop-direct | +1.310 | [-6.457, 9.078] |
| prepare-noop | +6.768 | [5.016, 8.520] |
| real-prepare | +29.980 | [23.346, 36.613] |
| real-ranked | -0.473 | [-4.663, 3.717] |
| ranked-direct | +38.531 | [25.979, 51.083] |

页面参数准备相对空任务增加约 6.77 ms/step；真实迁移路径相对准备再增加约
29.98 ms/step，两项区间均高于零。空任务与 Direct 的区间跨零，不能认为队列是
主要成本，也不能认为队列零成本。real 与 ranked 整步差异区间跨零，不等于统计等效。

所有正式步 late_unpacks=0。ranked 比 Direct 慢 38.531 ms/step，其中 forward
约增加 34.710 ms。需求处等待很小不代表后台预取没有干扰前向计算。
real 后台任务：绑定 0.597 ms、数组准备 5.627 ms、move_pages 45.046 ms、
状态检查 0.530 ms，migration 总计约 51.814 ms；prepare 的数组准备约 5.832 ms。
后台任务与训练并行，上述耗时不能直接加到训练步耗时。

**单位注意：steps.json 的 *_ns 为纳秒；summary.json 的 process_means、means、
contrasts_ms 时间数值已除以 1e6，单位为毫秒，内部键仍保留 *_ns 后缀。**

## 检查与解释限制

五个诊断进程最大数值误差均为 0，映射释放检查通过。ID10 初始 8192 页均在 node2；
使用时 direct/noop/prepare 保持 node2，ranked/real 在 node0。15 个计时进程的
完整 loss 序列、目标选择哈希、执行源码哈希一致。分析器验证步数、迁移量、任务数、
迁移数以及阶段之和等于整步。迁移控制 3 项新增测试及相关回归共 28 项通过；
Python 编译检查和 git diff --check 通过。

- prepare−noop 支持当前 Python/ctypes 参数构造有可观测成本；GIL 干扰仍是假设。
- real−prepare 还改变绑定、系统调用、状态检查、最终驻留和缓存状态，不是纯搬运成本的隔离。
- move_pages 墙钟包含内核工作、阻塞与调度，不是纯硬件传输耗时，不能据此归因于 CXL 带宽。
- 一个工作负载、一个目标、三次重复，未施加整进程 DRAM 上限；不能推广至所有张量。
- 9 月 23 日与本轮批次不同，结论只采用本轮内部对照。
- 尚无稳定预取赢家；本轮不能否定 AOL，也未证明收益感知策略有效。
- 使用 ALTO 内核不等于运行完整 SoarAlto 训练策略；本轮是显式 saved-tensor 适配层。

## 复现与结果位置

在 SoarAlto 根目录执行，输出目录必须不存在。分析器当前只支持三次重复。

    python3 research/cpu_training/run_migration_ablation.py results/migration-ablation-diagnostic-new --diagnostic
    python3 research/cpu_training/run_migration_ablation.py results/migration-ablation-timing-new --steps 100 --repeats 3
    python3 research/cpu_training/analyze_migration_ablation.py results/migration-ablation-timing-new
    python3 research/cpu_training/test_migration_ablation.py

本轮目录：results/migration-ablation-diagnostic-0929/、results/migration-ablation-timing-0929/。
保存 commands.json、environment.json、completed.json、日志、steps.json、manifest.json、
执行源码；诊断另有 correctness.json、events.json；计时另有 summary.json、analysis.log。
轻量版本管理证据见 [本轮归档](evidence/migration-ablation-0929/README.md)。

## 下一步

1. 保留旧路径对照，评估参数数组复用或本地实现；构建、分配、清理全部纳入端到端计时。
2. 先验证数值、驻留、失败传播、生命周期，再用相同调度/亲和性/随机块重复对照。
   每步地址可能变化，不可复用过期页地址；不能把准备工作移出计时边界制造加速。
3. 再通过分批、时机等受控实验分离真实迁移路径与计算干扰，必要时加入 PMU。
4. 执行器开销得到控制后，重新比较 Direct/初始 DRAM/预取，扩展对象并评估 AOL
   对收益预测的增量价值；暂不直接改造 AOL 或引入 ML。

## 页面参数向量化结果（2026-09-29）

完成七条件、21 个计时进程。真实迁移参数准备从 5.776 降至 0.215 ms，
但 move_pages 墙钟增加约 5.523 ms；整步从 143.365 降至 141.307 ms，
差异区间跨零，尚未证明稳定训练加速。优化版仍慢于 Direct。下一步验证
迁移开始时机与计算重叠，暂不改造 AOL。详见 [向量化对照报告](VECTORIZED_MIGRATION_REPORT.md)。

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
