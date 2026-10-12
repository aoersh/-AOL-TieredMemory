# 真实 backward 使用与放置收益的收敛验证（2026-10-12）

## 问题和预设停止条件

承接容量和对象选择实验，最后检查当前 attention 保存对象是否存在可利用的
DRAM 访问收益。先重放真实使用，若副本创建/使用完整成本有实用差异，则只追加
一组完整训练确认；不继续扫描迁移时机。实用门槛沿用方案既有的3%，不能把
算子局部的百分比当作训练加速百分比。五次独立进程、随机串行，保留全部结果。

## 比较对象、公平性与结论范围

本研究中的 Direct CXL 是 CPU 直接使用 CXL 保存副本，不主动迁移其页面；
Prefetch 是先创建相同 CXL 副本，再将页面从 node2 迁到 DRAM node0。
两者比较回答的是“对象已经保存到 CXL 后，迁移是否划算”。Direct 迁移0字节、
Prefetch迁移目标字节数是策略差异；公平要求同一初始驻留、目标大小与生命周期，
不是让不迁移条件也搬页。两组共同复制与策略新增的迁移成本都保留在完整计时中。

本轮大配置比较没有页面迁移：Direct 与 pack34 都逐步 fresh 创建全部合格副本，
都允许128 MiB受管DRAM额度，仅ID34的初始目标节点不同。它回答初始放置问题，
不能称为全部DRAM的原生训练参考。参数、输入、梯度和源张量不在额度账本内，
这也不是真正整进程快内存不足的对照。

受管方案使用 saved-tensor hooks 额外复制，原生 PyTorch 通常直接保留已有 storage。
因此，已测 Prefetch 负收益适用于当前副本管理、工作负载与实现，不推广到所有页面
迁移或所有CPU训练。未来容量实验需补充无额外复制的原生DRAM性能参考；该参考
用于量化框架开销，不能作为超出相同容量预算的策略竞争者。

10-08早期目标复用不对称等结果已撤回，见 [修正报告](CLOSURE_REPORT_1009.md)。
修正后的异步对照为Direct88.047/Prefetch107.036 ms；全部正式步骤无迟到，
需求等待约0.040 ms，主要整步差位于其余forward，而不是等待迁移完成。
内核LRU自旋是热点证据，不是墙钟占比；本轮forward改善、backward增加的根因
仍未确定，不能直接归因于内核、缓存或DRAM硬件劣势。

## 重放的真实性和成本口径

从实际 batch8、width128、heads4、layers2、FP32 Transformer 的原生 forward/backward
捕获 ID10/ID34，确认保存 tensor 的 grad_fn 是 SoftmaxBackward0。
通过该节点的 prehook 和 posthook 捕获实际到达的输出梯度及真实输入梯度。
重放调用相同 PyTorch CPU 算子 `aten::_softmax_backward_data`，无手写梯度公式。
真实保存值、梯度和预期结果的 SHA256 在各独立进程、两节点和诊断之间完全一致。
此重放只覆盖该保存对象的 softmax backward 使用，不代表整层 attention 或全部网络。

三个案例是 sequence512 的 ID10/ID34（各32 MiB）和 sequence1024 的 ID34（128 MiB）。
固定 CPU0–7、8 intra-op线程、DRAM node0/真实CXL node2，内核6.8.12-138-soaralto。
梯度及结果分配绑定node0；只改变保存副本节点。每次创建fresh副本、只使用一次并
关闭映射，没有人为增加读取复用、计算或sleep；没有人为flush缓存。
自然缓存状态紧接fresh copy，不能声称测的是纯冷内存延迟，或等同于完整训练的
forward到backward间隔。尤其128 MiB的算子同时读写其他数组，目标大小并非全部工作集。

分别报告创建、使用、释放和完整步骤。创建含mmap/mbind/frombuffer/copy及缺页；
使用含真实算子执行和结果分配，不是纯读取延迟。
诊断逐步查询驻留、检查数值，不能用其时间作性能结果。
计时步骤不逐步查询，仅最后一步做数值检查，检查成本仍保留在完整步骤和run，
单列checks_ms。完整run还包括捕获训练、数据hash和初始化，除以全部105步；
它是本pilot的摊销账目，不能叫作真实训练step/epoch。

## 重放结果

每条件五次独立进程，各5预热+100正式。差值为 CXL−DRAM，正数表示DRAM更快；
配对t(df=4)95%区间，未校正多重比较。同一进程中的步骤不是独立重复。

| 案例 | DRAM使用 ms | CXL使用 ms | 使用差 ms [95% CI] |
|---|---:|---:|---|
| seq512 ID10 | 2.405 | 2.488 | +0.084 [−0.001,0.169] |
| seq512 ID34 | 2.426 | 2.480 | +0.054 [−0.002,0.109] |
| seq1024 ID34 | 9.174 | 9.094 | −0.080 [−0.152,−0.009] |

两个32 MiB对象的使用差区间都跨零；128 MiB的DRAM使用略慢约0.88%，低于3%门槛。
没有得到稳健、实用的DRAM使用阶段优势，也未证明完全等效。

| 案例 | 创建差 ms [95% CI] | 创建＋使用＋检查＋释放差 ms [95% CI] |
|---|---|---|
| seq512 ID10 | +0.651 [0.509,0.793] | +0.471 [0.154,0.789] |
| seq512 ID34 | +0.611 [0.459,0.764] | +0.464 [0.165,0.763] |
| seq1024 ID34 | +3.045 [2.792,3.298] | +3.154 [2.882,3.426] |

128 MiB完整单次操作为DRAM25.181/CXL28.335 ms，差异约11.1%，主要来自创建，
不能当作页面迁移的潜在访问收益：迁移策略已付过CXL创建成本，不能追回它。
32 MiB完整差也主要来自创建，不能跨不同执行环境直接加到网络速度或减去迁移时间。

完整run摊销CXL−DRAM分别为0.522 ms [0.271,0.773]、0.503 ms [−0.401,1.406]、
3.383 ms [2.986,3.781]，捕获和检查开销未从run删除。

由于128 MiB有创建路径的局部收益，本轮按门槛追加唯一一个sequence1024完整训练
对照：两组都允许128 MiB受管DRAM额度，Direct不使用；pack34只将ID34初始放DRAM。
其他合格副本均fresh在CXL，参数/输入/梯度不属于该容量账本，没有全进程DRAM限额。
此对照检验初始放置收益，不称为预取或迁移策略收益。

## 入口和原始证据

- `run_backward_replay.py`：真实节点输入捕获、fresh单次重放与诊断。
- `run_backward_replay_matrix.py`：六条件，诊断各一进程、计时各五进程。
- `analyze_backward_replay.py`：真实数据hash、时间边界、节点、释放及配对区间审计。
- `validate_backward_replay.py`：六项内存故障注入，原始结果不改写。
- `archive_backward_replay.py`：按diagnostic/timing/source结构保留轻量证据。
- 大配置完整训练沿用容量runner新增 `--large-reference`。

```bash
python3 research/cpu_training/run_backward_replay_matrix.py results/replay-diag-new --diagnostic
python3 research/cpu_training/analyze_backward_replay.py results/replay-diag-new
python3 research/cpu_training/run_backward_replay_matrix.py results/replay-time-new --diagnostic-reference results/replay-diag-new
python3 research/cpu_training/analyze_backward_replay.py results/replay-time-new --diagnostic-reference results/replay-diag-new
python3 research/cpu_training/run_capacity_matrix.py results/large-diag-new --large-reference --diagnostic
python3 research/cpu_training/analyze_capacity_pilot.py results/large-diag-new
python3 research/cpu_training/run_capacity_matrix.py results/large-time-new --large-reference --diagnostic-reference results/large-diag-new
python3 research/cpu_training/analyze_capacity_pilot.py results/large-time-new --diagnostic-reference results/large-diag-new
```

新结果目录不得存在。原始结果为 results/backward-replay-{diagnostic,timing}-1012-v1/
和 results/large-placement-{diagnostic,timing}-1012-v1/。完整日志和步骤保留本机，
不修改内核/sysctl或重复perf采样。

## 完整训练确认及停止决定

追加的sequence1024网络每步合格副本为 **396.3125 MiB**，目标ID34为128 MiB。
两个条件都允许128 MiB受管DRAM额度；Direct实际占0，pack34实际峰值128 MiB。
各五独立进程、5预热+100正式，随机串行；先独立三步诊断，数值误差0，
全部创建/使用位置和释放账本通过。全程未调用页面迁移。

| 完整训练 | Direct全部副本CXL | 仅ID34初始DRAM | DRAM放置−Direct ms [95% CI] |
|---|---:|---:|---|
| 整步 ms | 284.384 | 284.565 | +0.180 [−2.110,2.470] |
| forward ms | 130.768 | 127.183 | −3.586 [−4.511,−2.660] |
| backward ms | 152.717 | 156.464 | +3.748 [2.220,5.276] |
| 完整run/105步 ms | 289.471 | 289.735 | +0.264 [−2.042,2.569] |

局部创建路径收益没有变为网络加速：forward的改善与backward增加几乎抵消。
阶段差不能直接归因于缓存、LRU或页表，新增这轮没有采样拆分这些原因。
本批主指标差值区间完全位于基线±3%（约±8.532 ms）内，也就是支持排除本配置
至少3%的初始放置收益；这不是证明两路径绝对等效，更不是全网络/全硬件的上界。
捕获模型、梯度、原始分配和kernel环境范围仍需按本报告限定。

**停止当前两层attention对象的迁移参数与DRAM放置搜索。** 已有同步迁移负收益、
异步ID10负收益、初始放置不确定和现在的大配置独立确认；当前没有实用“应迁移”
边界，不用本数据拟合AOL收益模型或复杂ML。不能把使用差0.08 ms与另一场景30 ms
迁移直接相减作因果拆分，只能说明使用收益尺度不足且完整路径没有通过验证。

这轮没有覆盖不同算子族、BF16、CNN、全进程DRAM压力或优化后的完整异步调度。
未来如继续，只预先选一个不同算子族的真实训练案例做一次有限筛查，或明确转到
真正容量压力下的时间/容量折中；在缺少现实依据时，不通过提高人为复用次数、
插入sleep或无限扩shape寻找正例。

继续/停止决策优先补足有实际依据的容量不足场景，并先验证数值、驻留、容量与
释放生命周期。先在相同实际DRAM约束下比较Direct与静态放置，按既定3%与噪声
门槛检验完整训练收益；通过后才测含全部搬运/回收/重置成本的动态迁移，再比较
AOL与简单静态或热度选择的增量价值。首道门未通过就停止该案例，不把额外实验
当作已完成结果，也不把当前对象的停止决定升级为整个研究方向无效。

38项相关回归、六项重放故障注入和三项网络账本故障注入通过；回归在两个矩阵
全部结束后运行。归档含完整原始文件hash、实际执行源码、五进程均值、区间、
环境和步骤锚点；完整步骤保留本机。轻量目录：
[backward-replay-1012](evidence/backward-replay-1012/README.md) 和
[large-placement-1012](evidence/large-placement-1012/README.md)。本阶段新增40个正式
独立进程（重放30、训练10）和8个诊断进程，不重复已有容量矩阵。
