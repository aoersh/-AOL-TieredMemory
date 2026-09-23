# 前向就绪队列按历史需求优先级迁移

日期：2026-09-22。继续检查预取基线的调度问题，本轮未实现收益选择或 AOL 预测。

## 机制和控制条件

新增 `ranked`：第一步同步校准，记录真实需求顺序。后续步骤在每个 pack
副本完成后，将该独立 buffer 放入单工作者的优先队列，优先级取上一轮的
首次需求顺序。只对已经就绪且尚未开始的任务排序，不暂停或抢占在途迁移。
因此前向仍可与迁移重叠，但未来尚未产生的高优先级对象不能被提前提交。

在 pack 时逐项比较上一轮的序号、shape 和排除原因。若前缀变化，后续对象
回退为需求时同步迁移，已经提交的任务正常完成；不会撤销已经发生的迁移。
下一轮使用最新轨迹。此为固定形状验证，前缀相同不保证任意动态图语义相同，
不宣称支持任意模型；不使用未来实际需求。

FIFO `async` 与 `ranked` 共用同一单线程队列实现，FIFO 使用相同优先级并
按提交序号出队；优先级模式使用历史需求顺序。第一步仅 ranked 校准为同步，
所有步骤成本均保存。其他对照继续使用原单线程执行器。每种模式只有一个
后台工作者，固定 CPU 8，计算使用 CPU 0–7；不同时启动两套迁移器。

对照包括受管 DRAM、Direct CXL、同步、FIFO async、反向边界历史顺序
`demand`、pack 时历史优先级 `ranked`。容量充足；受管页绑定 0/2，其他匿名
分配绑定 DRAM 0。全局设置未修改。两档 Transformer 配置沿用上一批。
FIFO 队列实现有调整，所有模式重新运行，不能用旧批次耗时做直接因果比较。

## 验证

新增三项测试：有阻塞任务时就绪队列按优先级重排且 CPU 固定为 8；
前缀变化后同步回退并保证重复 unpack 只迁移一次；工作者失败向需求线程传播。
工作项完成后显式丢弃引用，收尾用屏障等待任务引用释放。

12 次诊断（2 档 × 6 策略），每次三步，与原生训练逐步比较 loss、全部梯度、
更新后参数，最大误差均为 0，驻留/释放/候选集合和源码哈希检查通过。
ranked 的三步优先级提交数均为 0、35、35；后两步实际开始顺序不同于提交
顺序，确认发生重排，而不是只修改队列名称。

前向阶段与迁移执行区间存在重叠，但前向阶段包含复制、hooks 等，不能等同
于全部与纯算子计算重叠。后台迁移时间不直接加到计算时间上计算净收益。

计时：2 档 × 6 策略 × 5 独立进程，共 60 次，各预热 5 步、计时 20 步。
按轮随机策略顺序；以独立进程均值配对，95% t 区间 df=4，仅作探索。
保留全部 25 步训练时间总和（含首步校准/冷启动），不称为进程 wall time。

## 文件和运行

## 实测结果

60 个计时进程全部完成，五次独立重复的平均稳态 step 时间（ms）如下：

| 规模 | 受管 DRAM | Direct CXL | 同步 | FIFO async | 反向 demand | pack-time ranked |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| small | 13.013 | 10.524 | 19.582 | 17.945 | 17.463 | 17.243 |
| large | 115.089 | 91.014 | 181.790 | 177.697 | 180.921 | 168.786 |

相对 FIFO，ranked 的配对差值（FIFO−ranked）为 small +0.702 ms，95% 区间
[−0.080, +1.485] ms，跨 0；large +8.911 ms，区间 [+0.439, +17.383] ms。
大规模出现探索性改善，但仍只是 5 对进程、容量充足和固定形状结果，不能
称为通用收益或 Always-Prefetch 最优。

ranked 的平均需求等待为 small 0.402 ms、large 20.666 ms，FIFO 分别为
1.441 ms、47.221 ms；平均迟到 unpack 从 1/步降至 2.16、9.43/步（统计中
包含每步对象访问次数，不能只看一个迟到对象）。前向迁移重叠保持在 small
8.771 ms、large 54.789 ms。ranked 仍明显慢于 Direct：small −6.719 ms、
large −77.771 ms（Direct−ranked，区间均不跨 0）。

ranked 改善了 FIFO 的排队关键路径，但没有证明预取优于直接访问，也没有证明
收益在容量压力、不同模型或其他窗口下保持。当前 Q1 仍未形成充分的混合赢家
证据，下一步应进入少量单对象干预和复制/放置成本分解，而不是直接扩展 ML。

```bash
python3 research/cpu_training/run_path_matrix.py results/ranked-diagnostic-new \
  --phase diagnostic --modes dram direct sync async demand ranked
python3 research/cpu_training/run_path_matrix.py results/ranked-timing-new \
  --phase timing --repeats 5 --modes dram direct sync async demand ranked
python3 research/cpu_training/analyze_paths.py results/ranked-timing-new
python3 research/cpu_training/plot_paths.py results/ranked-timing-new
python3 research/cpu_training/test_ranked.py
```

本次数据目录：`results/cpu-training-ranked-diagnostic-0922/` 和
`results/cpu-training-ranked-timing-0922/`。含源码快照、环境、依赖、命令、
逐步数据、stdout/stderr、诊断驻留/迁移事件、汇总 CSV/JSON 和图表。
原始实验数据保持不变；统计脚本仍要求五次独立重复。
