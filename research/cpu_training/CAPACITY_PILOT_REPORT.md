# 有限 DRAM 受管副本池：放置与迁入决策 pilot（2026-10-09）

## 问题与协议

研究重点转向“不应迁移的场景、值得迁移的场景，以及有限 DRAM 应分给谁”。
本轮先建立按真实映射存活期约束的硬容量账本，不继续优化 ID10 搬页速度。
旧 budget 模式只限制每步累计迁入字节，不能回收容量，本轮使用独立新入口。

固定 batch8/sequence512/width128/heads4/layers2 FP32 Transformer，计算 CPU0–7，
迁移 CPU8，node0 DRAM/node2 CXL。所有合格 saved tensor 副本都纳入容量账本，
每步总创建 134.15625 MiB，每条件均 fresh；不同于以前仅 ID10 在 CXL 的场景，
不得跨实验直接比较绝对时间。参数、源 tensor、梯度、输入、优化器以及其他
PyTorch 分配不在池内，这是受管副本池容量限制，不是整进程 DRAM 限额。

| 条件 | 初始放置与动作 | DRAM 额度 |
|---|---|---:|
| direct | 全部受管副本在 CXL，直接使用 | 0 MiB（在其他额度下也可行） |
| pack32 | 保存顺序先到先得，能放入则初始放 DRAM，其余 CXL | 32 MiB |
| pack64 | 同上 | 64 MiB |
| demand32 | 全部初始 CXL，首次使用时有空位则同步迁到 DRAM | 32 MiB |
| demand64 | 同上 | 64 MiB |

这两种分配次序都是公开的简单参考，不是收益策略。demand 是同步需求迁移，
不能当作优化后的异步 Prefetch，更不能据此否定所有预取。
pack 是避免后续搬页的放置参考，初始状态与 demand 不同；二者比较完整执行路径，
差值不解释为纯迁移成本。预算不够直接使用 CXL，本轮不执行驱逐/回迁。
所有条件步末真实释放，新步重建；创建、迁移、等待与释放保留在 step，
一次性设置、检查和关闭保留在 total_run。

## 容量生命周期与验证

使用与底层 mmap 绑定的 finalizer 归还额度；返回 tensor/view 保持存活时，
不能因为 Packed wrapper 释放而回收额度。迁入前预留全部页对齐大小，迁移
成功后才能交给算子；迁移失败传播异常，不能用失败运行计算均值。
按 reserve/release 事件独立重算峰值、逐步占用和 byte-time，检查步末租约为零。
预算账本是用户映射/预留容量，不计内核瞬时 staging、缓存或未受管内存。

两个生命周期测试通过：别名保持容量且释放后可供下一对象使用；零预算
需求路径始终不迁移。五条件各三步诊断相对原生 loss/梯度/参数误差为零；
所有创建/使用驻留、源代码哈希、loss/选择序列、134.15625 MiB 创建量通过检查。
诊断时间不用于性能结论。

初始 v1 direct 冒烟、v2 五组诊断保留；v3 加入逐次租约事件后作为正式诊断参考。
正式矩阵为五条件×三次独立进程，每进程 5 预热+100 正式，种子 20261009
逐轮随机顺序，串行运行，不并行跑测试或其他研究负载。

## 代码与复现

- `run_capacity_pilot.py`：基于现有 saved hooks 的容量租约实现与单进程诊断/计时。
- `run_capacity_matrix.py`：五条件串行随机矩阵。
- `analyze_capacity_pilot.py`：重算峰值/释放与进程级配对分析。
- `test_capacity_pilot.py`：存活别名与零预算行为测试。

```bash
python3 research/cpu_training/test_capacity_pilot.py
python3 research/cpu_training/run_capacity_matrix.py results/capacity-diag-new --diagnostic
python3 research/cpu_training/analyze_capacity_pilot.py results/capacity-diag-new
python3 research/cpu_training/run_capacity_matrix.py results/capacity-time-new --diagnostic-reference results/capacity-diag-new
python3 research/cpu_training/analyze_capacity_pilot.py results/capacity-time-new --diagnostic-reference results/capacity-diag-new
```

所有输出目录必须不存在。原始数据：`results/capacity-pilot-diagnostic-1009-v3/`
和 `results/capacity-pilot-timing-1009-v1/`。10 月 12 日检查断点确认所有运行已完成，
未重复原矩阵。追加审计输出使用 `verified-analysis-1012-v2/`，原分析保留。

## 五条件正式结果

| 条件 | 整步 ms | 完整 run/105 步 ms | DRAM 峰值 MiB | 迁入 MiB/步 |
|---|---:|---:|---:|---:|
| direct | 90.395 | 95.511 | 0 | 0 |
| pack32 | 93.632 | 98.639 | 30.15625 | 0 |
| pack64 | 95.692 | 100.907 | 62.15625 | 0 |
| demand32 | 194.765 | 200.137 | 32 | 134.15625 |
| demand64 | 194.001 | 199.316 | 32 | 134.15625 |

采用三个独立进程均值的配对 t(df=2) 95% 区间，未校正多重比较。
pack32−direct 为 **3.237 ms [0.040,6.435]**，pack64−direct 为
**5.297 ms [−2.530,13.125]**，没有显示保存顺序初始放置的收益。
demand32/64−direct 分别为 **104.370 ms [102.419,106.320]** 和
**103.606 ms [94.571,112.641]**。含设置、全部预热和关闭的完整 run 摊销结果同方向。

需求迁移两档每步都累计搬入 134.15625 MiB，但释放后复用额度，峰值只用到
32 MiB；两档迁入对象集合相同。此时增加额度没有改变动作，也未解决同步需求迁移
的暴露成本。额外阶段时间主要位于 backward，不能把整步差当作纯物理复制时间。

这些结果限定于本受管副本池和当前同步策略。“有空位就迁入”明显增加成本，
但它不是强异步预取基线；没有验证到值得迁移的边界，也没有证明所有对象都不适合 DRAM。

## 固定 32 MiB 的对象选择确认（2026-10-12）

按实际保存清单，第一层和第二层同尺寸目标分别为 ID10、ID34，各 32 MiB。
两者在所有运行中用固定 ID/shape 选择，分析核对完整 loss 和选择序列。
新五条件为：Direct CXL；只给 ID10/ID34 初始 DRAM；只在需求时迁入 ID10/ID34。
所有条件的额度为 32 MiB，其他合格副本均在 CXL。每步仍 fresh，无额外复用或回迁。
这是固定选择的干预，不是依据本轮未来数据在线选择的收益策略。

这轮保存了新环境快照、C 库及源码哈希；旧 10-09 矩阵没有归档 C 库哈希或完整
矩阵级拓扑，旧数据因此保留此证据限制。新旧批次分别分析，不拼接均值或区间。
诊断三步每条件数值误差为零，创建/使用驻留、映射释放和峰值检查通过；
正式计时每条件三个独立进程、5 预热+100 正式、随机串行运行。

```bash
python3 research/cpu_training/run_capacity_matrix.py results/selection-diag-new --selection-matrix --diagnostic
python3 research/cpu_training/analyze_capacity_pilot.py results/selection-diag-new
python3 research/cpu_training/run_capacity_matrix.py results/selection-time-new --selection-matrix --diagnostic-reference results/selection-diag-new
python3 research/cpu_training/analyze_capacity_pilot.py results/selection-time-new --diagnostic-reference results/selection-diag-new
```

本轮 `dram_mib_ms` 是用户租约的时间积分：初始 DRAM 的额度事件从副本创建完成后
开始，需求迁移从迁移前预留开始，直至 mmap finalizer。它不包含初始 materialization
的 DRAM byte-time，也不提供内核临时 staging 或全进程物理驻留积分；峰值上限在
串行创建前判断，无多个对象并发预留。不能把该指标称为全进程 DRAM 使用时间。

## 对象选择正式结果与阶段判断

10-12 新增 15 个进程已全部完成，与对应五组诊断的执行源码和 C 库哈希一致。
每步预算约束、目标选择、原生计时等式、释放与数值检查通过。

| 条件 | 整步 ms | 完整 run/105 步 ms | 租约 MiB·ms/步 | 实际迁入 MiB/步 |
|---|---:|---:|---:|---:|
| Direct，额度32 MiB未使用 | 88.090 | 93.100 | 0 | 0 |
| pack10 | 89.624 | 95.090 | 2163.847 | 0 |
| pack34 | 87.864 | 93.022 | 790.102 | 0 |
| demand10 | 118.501 | 123.493 | 1012.949 | 32 |
| demand34 | 117.736 | 122.719 | 1001.356 | 32 |

相对 Direct 的配对整步差：

- pack10：**+1.534 ms [−5.981,9.050]**。
- pack34：**−0.226 ms [−4.050,3.598]**。
- demand10：**+30.412 ms [27.971,32.853]**。
- demand34：**+29.646 ms [26.690,32.603]**。

pack10−pack34 为 +1.760 ms [−5.888,9.408]，尚无可靠优先级，也没有证明等效。
pack34 平均快约 0.26%，低于方案既有 3% 实用门槛且区间跨零，不把它称为赢家。
需求迁移相对 Direct 的完整 run 摊销差仍为约 +30.393/+29.619 ms，主要差异位于
backward（+28.742/+28.227 ms），不能通过移出迁移或关闭成本制造收益。

固定 32 MiB 初始放置时，ID10 的租约积分约为 ID34 的 **2.739 倍**；即使大小相同，
保存到最后使用的时间不同，对有限 DRAM 的占用机会成本也不同。但当前并未测到
可重复的速度收益，因此不能仅依据占用较短就宣称应优先分给 ID34。需要同时估计
访问收益、占用时间和迁移净成本；AOL 的额外解释价值仍未验证。

阶段结论：当前两个对象的同步需求迁移应回退到 Direct；初始 DRAM 放置仍属
不确定，不能把本轮负结果推广到所有 tensor、shape 或容量受限系统。当前证据支持
把“不迁移”作为有效动作并纳入策略空间，而未形成“应迁移”的正收益场景。

下一步优先重放这两个对象对应的真实 backward 使用，隔离副本创建、访问及生命周期
成本；若出现实用访问收益，再验证一个不同算子/shape 和相同容量下的选择策略。
不继续扫描同步迁移参数，不用本轮数据直接拟合复杂 ML，不将同步需求迁移当作
Always-Prefetch/TierTrain 的性能上界或强基线。

新原始目录：`results/capacity-selection-{diagnostic,timing}-1012-v1/`；有效分析
为 timing 目录的 `verified-analysis/`。旧容量矩阵与新对象矩阵分别统计。
轻量证据按 `diagnostic/`、`timing/`、`source/`、`validation/` 归档到
`evidence/capacity-pilot-1009/` 和 `evidence/capacity-selection-1012/`。
