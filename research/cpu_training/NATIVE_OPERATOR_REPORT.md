# 原生训练阶段缺页与算子诊断（2026-10-14）

## 问题与范围

承接 [原生放置对照](NATIVE_PLACEMENT_REPORT.md)，继续默认分配器路径的归因。
本轮固定同一个FP32 eager Transformer，batch8/sequence512/width128/heads4/layers2、
SGD、确定性合成输入，计算CPU0–7、DRAM0/真实CXL2。
进程导入前单节点MPOL_BIND，无saved副本、无迁移或额外worker，不注册allocator。
不改内核、THP或全局sysctl，不扩模型、shape、预算，不做新的perf/sudo采样。

整体匿名放置包括非张量分配；共享文件页与file-backed私有COW另列。
没有DRAM限额，不能当作同预算策略或纯tensor访问延迟实验。

新增run_native_operator.py，单独记录zero_grad、forward（含loss）、backward、
update（含SGD、loss.item、图释放）的墙钟与minor/major faults。
各段计数严格相加等于整步。阶段计数器的读取有少量开销，两个节点采用同一循环。
旧run_native_placement.py及其结果未修改；两批绝对时间不拼接为优化效果。

## 运行矩阵与正确性

先完成两节点各三个独立profiler进程，再运行两节点各五个无profiler进程，
均为5预热+100正式。两个矩阵分别随机串行，使用独立固定顺序种子。
profiler仅记录最后10个正式步骤（索引95–104），CPU-only、record_shapes=True、
profile_memory=True、with_stack=False。三对诊断不是正式性能统计的重复。
无采样矩阵不创建profiler，不安装保存hooks；进程开始/结束的匿名驻留快照
位于step计时外，但保留在完整workload计时中。

所有16进程的初始参数/输入hash、105步loss、最终梯度/参数hash完全一致。
训练前后策略核验及匿名驻留快照通过，Python张量引用最终释放。
六个采样进程的主调用线程ATen调用计数与输入形状一致，未改变外层算子序列；
这不证明底层库的全部内部执行路径相同。

## 无采样确认

五对独立进程，配对t(df=4)95%区间，步骤不作为独立重复。

| 指标 | DRAM | CXL | CXL−DRAM ms [95% CI] |
|---|---:|---:|---|
| 整步 ms | 76.803 | 90.470 | +13.667 [4.869,22.465] |
| zero_grad ms | 0.788 | 0.487 | −0.301 [−0.661,0.058] |
| forward ms | 26.763 | 33.921 | +7.159 [0.448,13.869] |
| backward ms | 48.330 | 54.987 | +6.657 [4.218,9.095] |
| update ms | 0.923 | 1.075 | +0.152 [0.136,0.168] |
| workload_run/105步 ms | 83.401 | 97.271 | +13.870 [5.596,22.143] |
| 完整子进程区间/105步 ms | 95.237 | 109.441 | +14.205 [6.032,22.377] |

CXL/DRAM两组均值比对应慢约17.79%；配对相对差均值18.33%，区间
[5.44%,31.21%]，下界高于既定3%实用门槛。五对整步差均为正，分别为
22.855、10.808、17.430、13.263、3.977 ms，仍有明显进程间波动。
这独立确认了原生放置方向，不将其与上一批合并为十对相同协议的统计。

正式前/后20步平均时间为DRAM77.097/77.060、CXL91.718/89.075 ms；
所有前/后窗口的配对差也为正。仍不称长期稳态或固定的CXL性能系数。

## 阶段缺页

| 每正式步minor faults | DRAM | CXL | CXL−DRAM [95% CI] |
|---|---:|---:|---|
| 整步 | 83035 | 85844 | +2809 [−20820,26437] |
| forward | 30277 | 33884 | +3607 [−19194,26408] |
| backward | 52758 | 51960 | −798 [−2732,1135] |

zero_grad/update均约0.05–0.08次/步，major faults全部为0。
反向阶段五轮都慢，四轮CXL的反向缺页计数反而更少，只有一轮更多。
因此“CXL产生更多缺页”不是反向损失的必要条件；不能仅凭计数解释减速。
该结果没有排除单次缺页成本、页面首次写入、匿名页管理或实际算子读写成本。
forward的计数与耗时均有较大波动，尚未做分配干预的因果分解。

## CPU profiler候选

以下是三个配对诊断进程、每进程最后10步的主调用线程ATen self CPU区间，
按阶段和算子聚合，再按每步归一化。不是独立无采样时间，也不是所有工作线程
CPU周期总和；区间包含等待并行算子完成，不能直接解释为纯内存停顿。
内部嵌套事件使用self时间避免inclusive叠加；未归属主step线程的事件不纳入排名。

| 阶段/算子 | 每步调用 | DRAM ms | CXL ms | 诊断差 ms [95% CI] |
|---|---:|---:|---:|---|
| backward bmm | 8 | 11.528 | 13.886 | +2.359 [1.677,3.040] |
| backward softmax_backward_data | 2 | 6.258 | 7.778 | +1.520 [1.319,1.720] |
| backward mm | 18 | 8.236 | 9.590 | +1.354 [1.059,1.649] |
| backward div | 3 | 5.523 | 6.687 | +1.164 [0.796,1.531] |
| forward softmax | 2 | 6.470 | 7.560 | +1.090 [0.396,1.784] |
| forward copy_ | 21 | 1.781 | 2.864 | +1.083 [0.089,2.077] |

诊断区间为t(df=2)，有事后排名与多重比较，属于候选机制证据。
例如反向bmm的 `[32,512,512] × [32,512,32]` 形状每步4次，诊断差
+1.499 ms [1.371,1.628]；softmax backward两个输入都为 `[8,4,512,512]`。
这定位了一组具体计算及大数组，尚未证明应迁移某个张量。

算子也包含新结果分配。首个DRAM诊断的正self内存事件按步合计，反向bmm约
76 MiB、softmax backward约64 MiB、mm约31.5625 MiB；这些是profiler张量分配
账目，不是物理驻留、全部allocator流量或CXL总线字节。不能把相应算子差
全部称为读取saved tensor的收益。

profiler明确警告：采样前分配的部分内存，其释放事件未被追踪。
record_shapes/内存记录还可能改变引用生命周期、分配行为和记录开销，尤其
记录器本身也受进程放置影响。采样进程最后10步为87.618/96.192 ms，
无采样独立进程最后10步为76.804/90.043 ms，采样改变了观察到的差幅；
两者不是同进程严格配对，不能用差值量化profiler自身成本。
不将上表算子差相加还原无采样13.667 ms，也不计算完整内存峰值。

## 阶段结论与下一步

1. 原生训练的整体CXL减速再次出现，主要阶段仍是forward与backward；
   这不是迁移收益，也不是新分配器的高开销结果。
2. backward减速可以发生在缺页次数更少时；数量、每次缺页成本和实际读写必须
   分开分析。现有数据不能断言损失全部在用户态或全部在内核。
3. bmm、softmax backward与mm是有限后续干预的优先候选。关注范围应包含输入
   梯度、临时结果和首次写入，而不只统计autograd保存值的访问频率。

此前仅改变一个softmax保存副本的重放没有实用访问收益，与本轮不矛盾：
本轮改变多个操作数、输出及其他匿名分配的位置，而且采样环境不同。
不能跨两个场景相减，断言其中某一数组已经解释全部差异。

下轮优先只选择真实softmax backward，保持保存值与计算不变，对输入梯度、
输出节点和输出首次写入做小规模控制。先确认数值、驻留和生命周期，完整创建/
使用/释放成本保留；预触页与fresh输出只是机制诊断，不当作实际训练加速。
不人为提高读取复用次数，不扩大迁移/AOL或重写分配器。

## 复现与归档

```bash
python3 research/cpu_training/run_native_operator.py results/operator-profile-new --phase profile
python3 research/cpu_training/analyze_native_operator.py results/operator-profile-new
python3 research/cpu_training/run_native_operator.py results/operator-time-new --phase timing --profile-reference results/operator-profile-new
python3 research/cpu_training/analyze_native_operator.py results/operator-time-new --profile-reference results/operator-profile-new
python3 research/cpu_training/validate_native_operator.py results/operator-profile-new results/operator-time-new results/operator-audit-new
python3 research/cpu_training/archive_native_placement.py results/operator-profile-new results/operator-time-new results/operator-audit-new research/cpu_training/evidence/operator-new --exclude-name operator-events.json trace.json
```

所有输出目录必须不存在。实际原始结果：
`results/native-operator-profile-1014-v1`、`native-operator-timing-1014-v1`、
`native-operator-validation-1014-v1`，前两者用各自verified-analysis。
十项JSON错误输入检查全部正确拒绝，测试时钟确认晚于计时矩阵结束，原始不改写。
[轻量证据](evidence/native-operator-1014/README.md) 按diagnostic/timing/source/validation
组织，239个原始文件hash复核。完整trace、事件、步骤、映射与日志保留本机；
归档含逐进程算子/形状摘要，不能仅凭步骤锚点重算统计。
本阶段已全部结束，未提交/推送，保留此前未提交工作。
