# 单对象 PEBS 覆盖实验（2026-09-23）

目的：补齐六个对象的真实地址采样覆盖，不用新的采样周期来宣称训练加速，
也不提前将区间 AOL 改成新的收益指标。

## 配置

Direct 只把 ID 2、6、10、16、20、22 中的一个放在 CXL，其他候选在 DRAM，
与单对象性能干预的放置范围相同。每个目标三个独立进程，种子固定，每轮
随机目标顺序、串行运行。每进程预热 5 步、正式 500 步，启用生命周期追踪。

本轮通过显式 `cpu/event=0xcd,umask=0x01,ldlat=30/P` 配置 PEBS。
旧别名 `mem-loads,ldlat=30` 的实际 config1 是 0x1f；新配置经 evlist 验证为
0x1e（30）。采样周期从 1009 改为 127；PMU interval 仍是 10 ms，时钟为
CLOCK_MONOTONIC。不合并不同阈值/周期采集的原始样本数比较 hotness。

ID16 的独立 200 步校准获得 13 个正式匹配样本，因此此前零样本不是零访问。
校准保留在 `results/pebs-id16-calibration-0923/`，不计入正式三次重复。

## 统计边界

- 匹配必须同时满足存活期和虚拟地址的半开范围，排除预热，保留零样本对象。
- 每次记录采样数、覆盖的训练步数、PEBS weight 与样本所在 interval 的背景 AOL。
- 有地址的采样归属比“整个生命周期分摊 PMU”更明确，但背景 AOL 依然是
  进程级计数。PEBS weight、访问采样密度和 AOL 是三个不同指标。
- ldlat 阈值和采样周期带来选择偏差，采样数不等于实际访问次数。
- 本轮增加到 500 步，与历史 20 步性能干预训练轨迹长度不同；不直接进行
  跨实验回归，也不将六对象之间的相关性作为已有发表证据。
- 每次的事件属性和 LOST record 审计独立保留。没有 LOST record 并不意味
  所有访问都被采样。

## 运行

从仓库根目录执行，输出目录必须不存在：

```bash
python3 research/cpu_training/run_single_pebs_matrix.py results/single-pebs-matrix-0923
python3 research/cpu_training/summarize_single_pebs.py results/single-pebs-matrix-0923
```

单次参数化探针：

```bash
python3 research/cpu_training/run_pebs_probe.py results/pebs-new \
  --target-ids 16 --steps 500 --period 127 --ldlat 30
python3 research/cpu_training/analyze_pebs_probe.py results/pebs-new
```

## 结果

18 个独立进程全部完成，共 9000 个正式训练步骤。事件属性均通过检查，
未见 LOST record，正式受管匹配样本没有落入无效 PMU interval。

| 对象 ID | 三次样本数 | 每次覆盖步数范围 / 500 | 三次背景 AOL 范围 |
|---|---|---|---|
| 2 | 47, 49, 32 | 32–45 | 163.88–166.43 |
| 6 | 747, 748, 745 | 400–410 | 160.76–165.28 |
| 10 | 664, 616, 636 | 360–372 | 161.39–167.03 |
| 16 | 41, 40, 48 | 37–45 | 156.78–167.45 |
| 20 | 741, 747, 751 | 398–406 | 160.11–164.59 |
| 22 | 189, 193, 188 | 156–169 | 156.76–166.79 |

覆盖率分层在三次重复中保持：ID6/10/20 较高，ID2/16 较低。背景 AOL 的
对象间范围大量重叠；低覆盖对象的 PEBS weight 波动也较大。这只能说明当前
特征的分辨率及覆盖限制，不能证明原 AOL 不具有解释能力。未拟合相关性，
也未设计/部署新的 AOL 控制策略。

后续先把特征与性能对照统一到相同训练步数和阶段，再比较：原区间 AOL、
按地址采样的访问密度、采样 weight、对象大小、pack→unpack 提前窗口。
原 AOL 继续保留作基线；若验证对象条件化/阶段划分有增益，才提出改造，
避免将采样筛选带来的差异误当算法收益。

## 文件与验证

- `results/single-pebs-matrix-0923/summary.json`：每对象三次重复。
- `process-features.csv`：每进程原始特征、覆盖、大小与提前窗口。
- 每次运行目录包含 perf 原始数据、PMU interval、命令、源码快照、
  全部生命周期、地址匹配结果、实际事件属性及 LOST 审计。
- `analysis-source/` 保留采集/分析脚本及 SHA256。
- 半开时间/地址范围、地址重用、无效 PMU interval 的匹配回归通过；
  Python 编译及 `git diff --check` 通过。

## 500 步阶段对齐结果（2026-09-23）

完成六对象 Direct/ranked 各三次计时（36 进程），与独立 PEBS 特征的
完整 505 步 loss 和源码一致。平均预取净收益均为负，尚无稳定预取赢家；
六对象描述性相关性不足以否定 AOL。详见 [对齐实验报告](MATCHED_500_REPORT.md)。
