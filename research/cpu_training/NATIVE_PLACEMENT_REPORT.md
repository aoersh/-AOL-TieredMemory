# 默认分配器下的原生 DRAM/CXL 训练对照（2026-10-14）

## 问题与比较对象

补齐默认 PyTorch CPU allocator 下原生训练的两节点对照。此前已有保存副本
DRAM/CXL 对照、无保存副本的新 C10 分配器对照，以及默认分配器原生 DRAM
参考；这些结果保留。本轮只补默认分配器原生 CXL，并在同批测 DRAM 形成配对，
不将历史批次绝对时间与新结果相减。

固定 FP32 eager Transformer、batch8/sequence512/width128/heads4/layers2、SGD，
仍为确定性合成输入，用于系统性能和数值对照，不是生产训练收敛实验。
训练 CPU0–7，DRAM node0、真实 CXL node2，内核 6.8.12-138-soaralto，
PyTorch 2.6.0+cpu。没有迁移线程，也没有旧 Paths 策略记账。

进程在导入 PyTorch 前通过 numactl 设置单节点 MPOL_BIND；两组只改变
`--membind=0` / `--membind=2`。保留默认 CPU allocator，不注册新分配器，
不做保存副本，不主动迁移，不改变 THP 或全局 sysctl。
本轮 numa_balancing=1、perf_event_paranoid=-1，THP 为 madvise，均已记录。

这衡量的是进程匿名内存整体放置的影响，参数、输入、梯度、激活之外的匿名
分配也会改变节点。共享文件页和 file-backed 私有 COW 不作为全匿名驻留证据；
并不声称所有进程页都在一个节点。没有 DRAM 容量限制，也不是同预算策略比较。

## 诊断与计时边界

先做两个独立三步诊断进程，再随机串行计时：每节点五个独立进程，每进程
5 步预热、100 步正式，共 1000 个正式步骤与 50 个预热步骤。随机种子 20261014。
重复单位是进程，五轮配对 t(df=4) 95% 区间，未校正多重比较。
预先固定实用门槛 3%，不因本轮点估计扩展 shape 或扫描分配参数。

诊断仅通过 saved-tensor hooks 返回 detach 视图，不复制保存值。查询参数、输入、
保存/解包 storage 和梯度所在页面，每条件 438 条去重观察：30 条初始 storage，
每步 54 条保存、54 条解包和 28 条梯度。每种观察内部按地址/大小去重，不能将
这些存储字节或边界页简单相加，当作唯一张量量或同时驻留峰值。
全部已查询页位于预期节点；七个诊断时点的无 file 字段匿名映射也只在预期节点。
这些是诊断覆盖与时点证据，不是计时进程连续页位置追踪。

两节点初始参数/输入 hash、三步 loss/梯度/更新后参数 hash 一致。
正式计时无保存 hooks、无逐页查询；10 个正式进程的 105 步 loss 序列及最终
梯度/参数状态 hash 完全一致。get_mempolicy 在训练前后确认单节点绑定。
图释放及最终 Python 张量引用释放检查通过，不声称默认 allocator 将所有缓存
页归还操作系统。

整步包括 zero_grad、forward、loss、backward、SGD、loss.item 和图释放。
forward字段从zero_grad开始，包含loss计算；optimizer字段还包括取值与图释放。
workload_run 包括模型/输入构造、初始 hash、预热、最终检查与清理，除以全部
105 步；不包含导入与 JSON 序列化。另报父进程启动/等待子进程的完整区间，
其中包含导入、启动与结果序列化。它们都是本固定测试长度的摊销量。

## 结果

| 指标 | 原生 DRAM | 原生 CXL | CXL−DRAM ms [95% CI] |
|---|---:|---:|---|
| 整步 ms | 74.245 | 89.284 | +15.039 [6.046,24.032] |
| forward ms | 25.880 | 33.455 | +7.575 [1.073,14.077] |
| backward ms | 47.441 | 54.768 | +7.327 [4.753,9.901] |
| optimizer/取值/释放 ms | 0.924 | 1.061 | +0.137 [0.095,0.180] |
| workload_run/105步 ms | 80.610 | 96.158 | +15.548 [6.565,24.532] |
| 子进程完整区间/105步 ms | 92.260 | 108.268 | +16.008 [7.457,24.558] |

两组整步均值之比，CXL 比 DRAM 慢约 20.26%；逐轮相对差的均值为 20.95%，
95% 区间 [7.12%,34.79%]。两种比例的分母不同，不能混用。
相对差区间下界高于既定 3% 门槛，本批支持实用的原生 CXL 减速。

| 配对轮次 | DRAM ms/步 | CXL ms/步 | 差 ms |
|---|---:|---:|---:|
| 0 | 69.033 | 93.769 | +24.736 |
| 1 | 71.251 | 89.374 | +18.124 |
| 2 | 70.223 | 86.641 | +16.418 |
| 3 | 75.356 | 84.752 | +9.396 |
| 4 | 85.363 | 91.884 | +6.521 |

五轮方向一致，但幅度明显变化，不能把 20% 写成通用或固定的 CXL 惩罚。
DRAM 的正式前/后20步均值为74.502/75.578 ms，CXL为89.570/87.437 ms；
部分单进程有更明显漂移，例如 DRAM-r1 为68.505/75.107 ms。
结果应理解为既定5预热+100正式区间的性能，尚非长期稳态系数。
所有正式前/后20步配对差均为正，窗口选择没有改变本批方向。

minor faults 为 DRAM78378/CXL83272 次/步，配对差 +4893 的区间
[−17701,27488] 跨零；major faults 为0。RSS历史高水位均值578.789/589.358 MiB，
差值区间跨零。缺页和地址/堆复用行为有波动，当前没有新的perf采样或因果干预，
不能把15.039 ms归为纯CXL访问延迟、带宽、缺页或LRU锁贡献。

## 阶段结论与下一步

默认分配路径下，当前训练确实对整体匿名内存的 DRAM/CXL 放置敏感。
此前单个保存对象未显示实用收益，不能据此推广为整个训练对 CXL 不敏感；
本轮也不能与旧副本或新分配器绝对耗时拼接，量化某个对象的损失。

本轮没有搬页，已发现的是整体初始放置性能差，仍未找到动态迁移正收益边界。
它不证明哪个 tensor 值得迁移，也不是有限 DRAM 下的优化结果。
forward 和 backward 均存在损失，阶段时间包含分配、首次写入和计算，不能
仅凭阶段差称为纯算子访问损失。

下一阶段优先在默认分配路径定位受影响的算子/阶段和分配行为，必要时用一次
有限独立确认评估进程间波动；有明确机制后再扩展不同算子族。
当前新 C10 mmap 分配器保留为容量核算/正确性原型，暂停性能策略搜索；
池化不是研究继续的前提。不自动增加模型、shape、预算、迁移或AOL矩阵。

## 复现与证据

```bash
python3 research/cpu_training/run_native_placement.py results/native-diag-new --diagnostic
python3 research/cpu_training/analyze_native_placement.py results/native-diag-new
python3 research/cpu_training/run_native_placement.py results/native-time-new --diagnostic-reference results/native-diag-new
python3 research/cpu_training/analyze_native_placement.py results/native-time-new --diagnostic-reference results/native-diag-new
python3 research/cpu_training/validate_native_placement.py results/native-diag-new results/native-time-new results/native-audit-new
python3 research/cpu_training/archive_native_placement.py results/native-diag-new results/native-time-new results/native-audit-new research/cpu_training/evidence/native-new
```

所有输出目录必须不存在。实际原始结果为
`results/native-placement-diagnostic-1014-v1`、`native-placement-timing-1014-v1`、
`native-placement-validation-1014-v1`，前两者使用各自 verified-analysis。
九项在内存中破坏JSON输入的审计检查均正确拒绝错误节点策略、时钟、进程重叠、
最终状态、驻留、源码、CPU绑定、loss和诊断状态；原始输入不修改，检查在正式
矩阵结束后执行。

[轻量证据](evidence/native-placement-1014/README.md) 按diagnostic/timing/source/validation
归档，155个原始文件哈希逐一复核。完整步骤、内存映射、地址记录与日志留本机，
锚点不替代完整统计。未修改内核/sysctl，未执行新perf或sudo采样。
本轮已全部结束，代码/报告/证据尚未提交，保留10-13已有未提交工作。
