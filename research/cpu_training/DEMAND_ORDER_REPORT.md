# 按上一轮需求顺序调度预取：实验记录

日期：2026-09-22。本轮继续 v3 方案，验证先前发现的 FIFO 需求顺序倒置是否
能够通过简单调度修复，并检查整步训练收益。原始 FIFO 数据保持不变。

## 新策略与公平性

新增 `demand` 模式：第一步按需同步迁移，并记录受管对象首次 unpack 的顺序；
后续迭代在前向完成、反向开始前，按上一轮需求顺序向单工作者提交全部候选。
对象序号、shape 和排除原因必须与上一轮一致；不一致则当轮回退同步路径并
重新记录。预测不读取未来需求，不注入 sleep 或额外计算来制造 overlap。

这个版本解决的是排队顺序问题，代价是放弃前向阶段的迁移窗口。它并非
完整 deadline/stay-time 调度器，不是 TierTrain，也不是收益选择策略。
因此比较结果只能评估这次调度变化，不能证明已经找到最优预取基线。

计算 CPU 0–7、迁移工作者 CPU 8、8 个计算线程；非受管匿名分配绑定 DRAM 0。
每个策略均保留相同工作者资源和收尾屏障。容量充足，受管副本使用 MPOL_BIND，
无显式 DRAM 限额，未启用在线 ALTO 或 PMU 采样。

两层显式 attention Transformer：width=128、heads=4；small 的 batch=4、
sequence=128，large 的 batch=8、sequence=512。FP32、SGD、固定输入和种子。
沿用受管 DRAM、Direct CXL、同步迁移和 eager FIFO async，新增 demand。
所有策略重新运行，不能直接与上一批绝对耗时混比。

## 正确性与统计口径

10 次诊断运行，各三步，都与同一初态的原生训练比较 loss、全部梯度和更新后
参数。最大误差为 0，页位置与映射释放检查通过，候选集合一致。demand 的
第一步校准，第二、三步提交顺序与实际需求一致，没有使用未来需求。

额外边界测试验证上一轮顺序的复用、重复 unpack、shape 变化时回退，以及
已学习调度中的迁移异常传播；原异步测试保留在途生命周期和释放检查。

计时矩阵为 2 档 × 5 策略 × 5 个独立进程，共 50 次；各进程 5 步预热、
20 步计时。策略顺序随机化。区间按 5 对独立进程均值计算，采用 df=4 的
探索性 95% t 区间，不把同进程 steps 当独立样本。

稳态时间外还记录全部 25 步的训练时间之和，包含首步校准和预热。该总量不是
启动到退出的 wall time，首步也含框架冷启动，不能将其全称为调度校准的增量成本。
阶段中 backward_ns 包括提交调度和需求等待，schedule_ns 单独记录；没有用
后台迁移总时长直接加到计算时长上。诊断时间不进入性能统计。

## 结果文件与复跑

### 实测结果

所有 50 次计时完成，源码快照哈希及候选序列一致性检查通过。
平均稳态 ms/step：

| 规模 | 受管 DRAM | Direct CXL | 同步迁移 | FIFO async | 上一轮需求顺序 |
| --- | ---: | ---: | ---: | ---: | ---: |
| small | 13.069 | 10.200 | 19.899 | 17.831 | 17.155 |
| large | 116.133 | 90.900 | 182.588 | 178.129 | 182.554 |

FIFO minus demand 的配对差值：small 为 +0.675 ms，95% 区间
[−0.223, +1.574] ms，跨 0；large 为 −4.425 ms，区间
[−8.652, −0.197] ms。小规模没有稳定改善证据；大规模 demand 更慢，差异
约 2.5%，也没有达到计划中约 3% 的初始实用门槛，不能夸大其实际意义。

Direct minus demand：small −6.955 ms，区间 [−8.486, −5.424] ms；
large −91.654 ms，区间 [−97.503, −85.805] ms。两档中 demand 都明显慢于
当前 Direct 路径。所有计时步都成功使用上一轮调度，没有 shape 回退。

包含预热的全部 25 步训练时间之和，FIFO/demand 分别为 small
455.007/453.882 ms、large 4465.862/4601.561 ms。其口径仍是训练 step
总和，不含进程启动、模型构造和输出文件写入。

### 阶段分解与解释边界

large 的前向/反向平均耗时分别为：

- Direct：41.273 / 48.741 ms。
- 受管 DRAM：50.766 / 64.212 ms。
- FIFO async：66.914 / 109.957 ms。
- demand：41.497 / 139.867 ms。

demand 减少了前向迁移干扰，但迁移/等待落到反向，未改善整步。
large 的需求等待仍为 FIFO 46.180 ms、demand 46.877 ms；迟到次数从每步
1 次变为约 14.57 次。顺序正确不等于总等待更少，迟到率也不能替代关键路径时间。
这些阶段包含 hooks、复制或等待，不是纯算子时长。

Direct 对受管 DRAM 的优势在前向和反向都出现，仍不能归因为 CXL 介质更快。
尚未单独测 buffer 分配/复制，也没有 PMU 证据；带宽、缓存、分配路径都可能
参与，当前只完成阶段定位，没有完成根因解释。

### 结论与下一步

排队顺序倒置在新模式中被消除，但两类“全部预取”都没有胜过 Direct。
Q1 仍未证明，AOL 和收益策略尚未进入有效性验证。下一步应在已有真实窗口内
尝试按预测需求优先处理就绪对象、结合迁移耗时估计提前触发，同时保留本轮
边界提交策略；不能无止境调参寻找赢家。先补齐分配/复制分解与少量单对象
干预，确认不利结果是否由执行器开销、窗口或对象选择导致，再决定是否扩大。
没有理由直接进入复杂 ML。

20 项回归测试通过：需求顺序 2 项、异步 3 项、基础边界 6 项、原项目 9 项。
全局设置保持 1/16/4（NUMA balancing/pte_scale/perf 权限）、demotion=false。

```bash
python3 research/cpu_training/run_path_matrix.py results/demand-diagnostic-new \
  --phase diagnostic --modes dram direct sync async demand
python3 research/cpu_training/run_path_matrix.py results/demand-timing-new \
  --phase timing --repeats 5 --modes dram direct sync async demand
python3 research/cpu_training/analyze_paths.py results/demand-timing-new
python3 research/cpu_training/plot_paths.py results/demand-timing-new
python3 research/cpu_training/test_demand.py
```

本机目录：

- `results/cpu-training-demand-diagnostic-0922/`：10 次诊断与源码快照。
- `results/cpu-training-demand-timing-0922/`：50 次运行、命令、stdout/stderr、
  `steps.json`、环境、源码快照、`process-means.csv`、`summary.json` 和图表。

分析脚本仍使用 n=5 的 t 临界值，不应直接用于其他重复数。
