# 预取无收益原因验证：同批次三方对照

本轮问题：在已测小型 Transformer 的单 tensor 对照里，没有观察到稳定预取
收益，究竟是 DRAM 放置优势小，还是迁移流程抵消了优势？

## 设计与判读边界

每个目标 ID 2/6/10/16/20/22，以随机策略顺序比较受管 DRAM、Direct CXL、
ranked 三种路径，各三个独立进程、5 步预热 + 500 步正式训练。三者使用
同一副本/hooks、同模型/种子/线程，其他候选均在 DRAM。DRAM 模式在各目标
组中实际都是全部候选 DRAM，重复运行用于提供邻近时间的基线。

本批不用 perf，不启用生命周期追踪，不改变 SoarAlto 核心算法或系统参数。
预取成本保留在训练 step 内。正式计时前，三模式各 3 步的数值/梯度/更新后
参数及页面位置诊断通过。

- `Direct − DRAM`：静态放置差异，含目标副本写入和后续读取等影响，不是
  纯读取延迟或严格理论收益上限。
- `Direct − ranked`：实际预取净收益，正值表示预取更好。
- `ranked − DRAM`：两种完整路径差，不能全称为纯物理迁移时间。
- 前向、反向、优化器及 drain 的差值用于定位开销出现的阶段，不直接当作
  CPU stall 或缓存失效的硬件证据。

区间使用三个配对独立进程差值的 Student t 分布（df=2），不将 500 步当
独立重复；对象间区间未作多重比较校正。分段结果留存，不删除慢进程。

## 运行

```bash
python3 research/cpu_training/run_single_matrix.py results/placement-triad-diagnostic-0923 \
  --diagnostic --targets 2 --modes dram,direct,ranked
python3 research/cpu_training/run_single_matrix.py results/placement-triad-500-0923 \
  --repeats 3 --steps 500 --modes dram,direct,ranked
python3 research/cpu_training/analyze_placement_triad.py results/placement-triad-500-0923
```

## 小配置结果

54 次独立进程计时全部完成。各目标的三个配对结果如下，时间单位均为 ms。

| ID | DRAM | Direct | Ranked | Direct−DRAM [95% CI] | Direct−Ranked [95% CI] |
|---|---:|---:|---:|---|---|
| 2 | 13.214 | 13.222 | 13.654 | 0.009 [-0.516, 0.533] | -0.432 [-1.207, 0.343] |
| 6 | 13.315 | 13.120 | 13.782 | -0.196 [-0.924, 0.532] | -0.663 [-1.809, 0.484] |
| 10 | 13.463 | 13.252 | 13.967 | -0.210 [-0.513, 0.093] | -0.715 [-1.553, 0.124] |
| 16 | 13.425 | 13.306 | 13.524 | -0.119 [-0.716, 0.479] | -0.218 [-0.896, 0.461] |
| 20 | 13.201 | 13.217 | 13.616 | 0.015 [-0.221, 0.252] | -0.400 [-1.371, 0.572] |
| 22 | 13.258 | 13.267 | 13.873 | 0.009 [-1.204, 1.223] | -0.606 [-1.313, 0.102] |

各对象区间均跨零，不能声称每个对象都显著偏好 Direct。六对象等权平均为
DRAM 13.313、Direct 13.231、ranked 13.736；平均额外成本约 0.505 ms/step。
三个轮次的集合平均 Direct−ranked 均为负；该事后集合统计见
`fixed-panel-summary.json`，不替代各对象的不确定性。

ranked 迁移量为每步 0.25 或 1 MiB，后台任务墙钟时长约 0.60–1.29 ms，
unpack 等待约 0.016 ms，9000 个 ranked 正式步骤中迟到次数为零。额外
阶段均值主要出现在 forward（每目标约 +0.23–0.45 ms），并非需求处等待。
时间重叠不代表成本免费；主线程本身变慢也可能让预取看起来“按时完成”。

## 大配置验证

为检查小工作集的限制，追加 batch=8、sequence=512、ID10 的三方对照，
目标 tensor 为 [8,4,512,512] FP32，32 MiB。其他候选仍在 DRAM；目标大小
从小配置 1 MiB 扩大到 32 MiB。本组每策略三个独立进程，预热 5 步、计时
100 步；它是独立配置验证，不与 500 步数据合并做相关性。

```bash
python3 research/cpu_training/run_single_matrix.py results/placement-large-diagnostic-0923 \
  --diagnostic --targets 10 --batch 8 --sequence 512 --modes dram,direct,ranked
python3 research/cpu_training/run_single_matrix.py results/placement-large-100-0923 \
  --repeats 3 --steps 100 --targets 10 --batch 8 --sequence 512 --modes dram,direct,ranked
python3 research/cpu_training/analyze_placement_triad.py results/placement-large-100-0923
```

| 路径 | ms/step |
|---|---:|
| 受管 DRAM | 114.251 |
| Direct CXL | 107.894 |
| ranked 预取 | 140.307 |

Direct−ranked 为 −32.413 ms，95% CI [−36.759, −28.068]；ranked 较 Direct
慢约 30.0%。Direct−DRAM 为 −6.357 ms，但区间 [−21.910, 9.196] 跨零，
不能据此声称 CXL 静态放置显著优于 DRAM。

ranked−Direct 的阶段均值差：forward +29.048 ms、backward +3.155 ms、
optimizer/drain +0.210 ms。unpack 等待均值仅 0.046 ms，300 个 ranked
正式步骤没有迟到。后台迁移任务墙钟均值 49.744 ms，与计算重叠，不能
直接将它加到 step 中，也不能把它称为纯硬件数据传输时间。

## 初步结论及未证实机制

1. 在当前受管 saved-tensor 原型中，尚未测出稳定的“初始 DRAM 放置使训练
   更快”的优势，因此不能默认搬回快层就必然获益。它包含写入、读取、
   缓存及执行路径因素，不意味着 DRAM 的硬件访问延迟高于 CXL。
2. 当前 ranked 预取流程会带来额外训练开销，大配置有明确的负收益证据，
   而需求处等待很小，额外阶段耗时主要在 forward。单纯优化到达期限，或
   将 AOL 调大/调小，都不能由现有证据推出会解决问题。
3. 页迁移本身、Python/ctypes 准备与线程调度、缓存扰动和带宽竞争仍未分离；
   当前观察不能把 +29 ms 唯一归因给其中任何一项。Direct 使用普通线程池，
   ranked 使用优先级工作队列，两种执行器的管理开销尚未独立对照。下一步可做相同调度但
   不搬页的控制，以及迁移准备/系统调用分解，验证软件与数据移动贡献。
4. 本轮没有产生稳定预取赢家，不满足训练选择性策略的正负收益覆盖。原 AOL
   尚不能被判无效；后续应保留原指标，并把迁移成本及前向干扰作为候选输入。
   当前不进入 ML 或在全负样本上调预取阈值。

不是整机 DRAM 容量受限实验，没有验证卸载/驱逐带来的容量收益，也不证明
所有 DNN/CXL 工作负载都不值得预取。CPU governor 为 powersave，未固定频率；
独立进程重复与随机策略顺序缓解但不能消除系统波动。参数保持原值。

## 证据与验证

共 63 次正式计时（27900 步），另有两档配置各三模式共 6 次正确性运行，
loss、梯度、更新后参数最大绝对误差均为 0，驻留及释放验证通过。计时数据
的完整 loss/候选序列、源码一致性及阶段时间相加等于 step 的检查通过。
分析边界测试、Python 编译和 diff 空白检查通过。

结果目录：`results/placement-triad-500-0923/`、
`results/placement-large-100-0923/`；`triad-summary.json` 包含进程级均值、
配对区间、阶段与迁移数据。诊断目录见运行命令。
