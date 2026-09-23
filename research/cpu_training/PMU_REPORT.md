# PMU/AOL 离线探针结果

> 2026-09-23 审计更正：下文历史“tensor 生命周期/窗口估计”实验仅保存最后
> 一步 35 条记录，且 pack→首次 unpack 不包含真正读取及释放阶段。不同对象
> 共享 interval 的 AOL 不能作为对象特征；这些结果不支持 AOL 有效或无效的
> 判断。整进程 PMU 探针不受这两个生命周期日志错误影响。后续结果见
> [测量审计与六对象对照](MEASUREMENT_AUDIT_0923.md)。

日期：2026-09-22。用户临时将 `kernel.perf_event_paranoid` 设为 `-1` 后，
使用项目内 `kernel/build-perf/perf` 完成 Direct、budget 1 MiB 和 ranked
各 5 个独立进程的采集。每次运行预热 5 步，随后计数 100 步；perf 事件在
训练窗口开始前启用、最后一步后关闭。窗口控制记录在每个运行目录的
`pmu-window.json`，原始计数在对应 `.csv`。

本机为 Intel Xeon 6515P（Granite Rapids）。使用的事件为：

```text
CYCLE_ACTIVITY.STALLS_L3_MISS             event 0xa3 umask 0x06 cmask 6
OFFCORE_REQUESTS_OUTSTANDING.DEMAND_DATA_RD       0x20/0x01
OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD 0x20/0x01 cmask 1
OFFCORE_REQUESTS.DEMAND_DATA_RD                    0x21/0x01
```

按 SoarAlto 定义计算：`AOL = outstanding_cycles / demand_data_reads`。

| 策略 | AOL | L3 miss stall / cycles |
|---|---:|---:|
| Direct | 166.11 | 0.00960 |
| budget 1 MiB | 169.70 | 0.00935 |
| ranked | 161.83 | 0.01002 |

三组事件均为 100% running，缺失计数为 0。结果只能说明这些**整进程训练窗口**
的 PMU 差异；当前 hook 没有建立 PMU interval 与 saved-tensor 地址生命周期
的对应关系，因此不能把 AOL 归因到某个 tensor，也不能据此宣称 AOL 已经
成功预测 Direct/Prefetch 收益。尤其是 ranked 的 AOL 较低并未带来更快 step，
说明 AOL 单指标不能直接当作策略收益。

下一步如果继续 AOL，应复用 `src/soar/run/profile_intervals.py` 的半开区间
和地址匹配逻辑，把真实 buffer 生命周期、PMU interval 和 tensor identity
关联后再做相关性分析。当前 Q1 尚未发现稳定混合赢家，因此不进入在线 AOL
收益策略实现。

结果目录：`results/cpu-training-pmu-window-0922b/`。

## 已解决的时间原点问题

项目内 perf 的 origin 输出由环境变量控制。正确命令必须带：

```bash
SOAR_STAT_CLOCK=1 ./kernel/build-perf/perf stat -I 100 -x, -o perf.csv ...
```

设置后输出包含：

```text
# SOAR_MONOTONIC_REF_NS <ns>
```

已用该方式重跑 Direct CXL 20 步计时，得到 21 个 interval、35 个 tensor
生命周期记录，35/35 个 tensor 成功匹配到至少一个 interval。结果在
`results/tensor-pmu-origin-0922/`，其中 `tensor-pmu-match.json` 是第一份
有效的时间窗口匹配结果。此前没有 origin 的旧 PMU 目录仍保留，但不能用于
tensor 归因。

完整命令模板：

```bash
SOAR_STAT_CLOCK=1 ./kernel/build-perf/perf stat -I 100 -x, \
  -o results/tensor-pmu-origin-0922/perf.csv \
  -e cycles,instructions,\
cpu/event=0xa3,umask=0x06,cmask=6,name=CYCLE_ACTIVITY.STALLS_L3_MISS/,\
cpu/event=0x20,umask=0x01,cmask=1,name=OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD/,\
cpu/event=0x21,umask=0x01,name=OFFCORE_REQUESTS.DEMAND_DATA_RD/ -- \
  numactl --physcpubind=0-8 --membind=0 python3 \
  research/cpu_training/run_access_paths.py \
  results/tensor-pmu-origin-0922/run --mode direct \
  --target-ids 2,6,10,16,20,22 --steps 20 --warmup 5

python3 research/cpu_training/match_pmu_intervals.py \
  results/tensor-pmu-origin-0922/perf.csv \
  results/tensor-pmu-origin-0922/run/tensor-lifetimes.json \
  "$(awk '/SOAR_MONOTONIC_REF_NS/{print $3}' \
    results/tensor-pmu-origin-0922/perf.csv)"
```

## Tensor 生命周期日志

训练入口现在额外生成 `tensor-lifetimes.json`，记录每个候选 buffer 的
`address/bytes/pack_ns/unpack_ns/step/id`。已在 Direct 运行中验证日志完整，
并用 `perf -I 100` 生成了 interval 原始文件。匹配工具要求调用者显式提供
monotonic origin，缺少 origin 时会拒绝匹配，避免把相邻进程的时间窗口错误
对应到 tensor；现在通过 `SOAR_STAT_CLOCK=1` 已获得第一份有效匹配结果。

## 首次 tensor 窗口估计

新增 `aggregate_tensor_pmu.py`，对 tensor 生命周期覆盖的 interval 按重叠
时间加权，输出窗口 AOL 和 L3 stall/cycle。由于 PMU 计数仍是整进程的，
这是共享窗口估计，不是逐地址计数；生命周期只决定时间覆盖范围。

在 Direct、budget 1 MiB、ranked 各 3 次短采集中，所有候选 tensor 均能匹配
到 interval。每策略的窗口 AOL 汇总如下：

| 策略 | tensor 窗口 AOL 均值 | tensor 样本标准差 | 平均 step (ms) |
|---|---:|---:|---:|
| Direct | 127.64 | 37.00 | 13.45 |
| budget 1 MiB | 120.35 | 16.12 | 14.00 |
| ranked | 135.37 | 7.60 | 14.44 |

平均生命周期只覆盖约 6.3% 的 100 ms interval，因此样本量和时间覆盖仍然
有限；不同 tensor 经常共享同一 interval。该结果不能证明 AOL 可以选择
访问路径，也没有发现稳定的 AOL 与 step 优势对应关系。结果目录为
`results/tensor-pmu-strategy-0923c/`。

## 单对象采样覆盖更新（2026-09-23）

已完成六目标各三次、每次 500 步的 Direct 单对象 PEBS 采集；18 次全部
成功，ID2/16 的零样本问题已转为可观察的低覆盖。对象背景 AOL 区间重叠，
暂不据此否定原指标。训练步数与旧计时对照不同，不直接跨实验拟合收益。
详见 [单对象 PEBS 覆盖报告](SINGLE_PEBS_REPORT.md)。

## 500 步阶段对齐结果（2026-09-23）

完成六对象 Direct/ranked 各三次计时（36 进程），与独立 PEBS 特征的
完整 505 步 loss 和源码一致。平均预取净收益均为负，尚无稳定预取赢家；
六对象描述性相关性不足以否定 AOL。详见 [对齐实验报告](MATCHED_500_REPORT.md)。

## 预取原因验证的初步结论（2026-09-23）

完成两档三方对照共 63 次计时：未发现稳定初始 DRAM 放置优势；大配置
32 MiB 目标的 ranked 比 Direct 慢约 32.4 ms/step，约 29.0 ms 出现在
forward，需求处等待仅约 0.046 ms。预取流程额外成本是当前主要问题，
具体的软件/迁移/缓存机制尚未分离，不据此否定 AOL。见
[三方对照与初步结论](PLACEMENT_CAUSE_REPORT.md)。
