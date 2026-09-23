# 2026-09-23 测量审计与六对象对照

## 修正此前结论的依据

此前生命周期日志在每个 begin() 清空，返回值只包含最后一步的 35 条记录，
不是完整 25 步的对象记录。pack→首次 unpack 表示等待第一次需求的窗口，
不能代表实际读取窗口或完整存活期。100 ms interval 内多个对象共享计数，
按重叠比例同时缩放 AOL 分子与分母不会增加对象区分能力。

因此历史 `tensor-pmu-strategy-0923c` 等窗口估计仅保留作调试记录，不用于
证明 AOL 无效，也不用于证明某个对象的 AOL 有效。9 月 22 日整进程训练
窗口 PMU 采集的独立结论不受日志截断影响。

## 修复

- 所有迭代的记录汇总输出，并明确 step 和 measured（排除预热）。
- 同时记录 pack、每次 unpack 和 mmap wrapper 的最终释放回调；回调只持有
  元数据，不延长 buffer 生命周期。记录范围从复制完成后开始，不含分配及复制。
- 生命周期追踪默认关闭，使用 `--trace-lifetimes` 显式开启。含追踪/PEBS 的
  运行只做诊断，不用于声称性能优势。
- 在每步输出中保存 CLOCK_MONOTONIC 起止时间。
- 映射范围仍仅覆盖合格受管副本，参数、输入、非连续 view 等不在覆盖范围内。

`results/lifetime-audit-0923/` 共 4 步、140 条记录，逐条验证
pack ≤ 首次 unpack ≤ release，数值正确性通过。额外测试验证：即使
packed wrapper 已释放，只要返回 tensor 的 view 仍存活，释放回调不会提前触发。

## 六对象实验

对象 ID：2、6、10、16、20、22。只改变一个目标的访问路径，其余候选保持
DRAM。每目标 Direct、sync、ranked 各 5 个独立进程，5 步预热 + 20 步计时；
同重复内策略顺序随机打乱。所有计时不启用生命周期追踪。

```bash
python3 research/cpu_training/run_single_matrix.py results/single-six-diagnostic-0923 --diagnostic
python3 research/cpu_training/run_single_matrix.py results/single-six-timing-0923 --repeats 5
python3 research/cpu_training/analyze_single.py results/single-six-timing-0923
```

## PEBS 地址归因探针

```bash
python3 research/cpu_training/run_pebs_probe.py results/pebs-address-probe-0923
python3 research/cpu_training/analyze_pebs_probe.py results/pebs-address-probe-0923
```

使用 CLOCK_MONOTONIC 的 PEBS 时间戳和 perf stat 的真实原点；匹配条件同时
满足 `[address,address+size)` 与 `[pack,release)`，跨步地址重用不能合并。
ldlat 配置为 30、采样周期 1009 的 load 样本并非所有访存，不等同于总 hotness。
10 ms PMU interval 依然是进程级背景 AOL。采用样本地址关联只改善访问归属，
并不能将共享 PMU 计数精确分摊到 tensor，也不将 PEBS weight 改名为 AOL。

## 本轮结果

18 次正确性运行全部通过（最大数值误差 0），90 次独立进程计时全部完成。
配对区间采用五个独立进程差值的 Student t 区间，不将同进程的 20 步当
独立样本；六个对象的区间未作多重比较校正，仅作探索性证据。

| ID | Direct ms | Sync ms | Ranked ms | Direct−Ranked ms [95% CI] |
|---|---:|---:|---:|---|
| 2 | 13.377 | 13.353 | 13.646 | -0.269 [-0.689, 0.150] |
| 6 | 13.235 | 13.609 | 13.691 | -0.456 [-0.808, -0.104] |
| 10 | 13.068 | 14.111 | 13.688 | -0.620 [-0.908, -0.332] |
| 16 | 13.229 | 13.621 | 13.626 | -0.397 [-0.523, -0.271] |
| 20 | 13.132 | 13.555 | 13.703 | -0.570 [-1.160, 0.019] |
| 22 | 12.984 | 13.960 | 13.773 | -0.789 [-1.034, -0.544] |

六个对象 ranked 平均值均慢于 Direct。ID 2/20 差异区间跨零，其余四个
区间在零以下。尚未出现稳定预取赢家；此结论仅限当前小 Transformer 的
单对象隔离设置，不支持“所有 DNN 都不值得预取”。

PEBS 探针保留 105 步、3675 条受管记录；总采样 4684，匹配受管对象 275，
其中预热 9、正式步骤 266。正式匹配的 266 个样本全部在首次 unpack 后。
未匹配的 4409 个样本包括初始化、非受管范围或注册存活期之外的数据，不是
自动等同于丢样。检查原始 perf 数据未见 LOST record；原始记录及审计保留。

六个指定目标的正式样本数为 ID2=1、ID6=22、ID10=16、ID16=0、ID20=18、
ID22=5。这个样本量不足以评估对象 AOL 与净收益的相关性，0 样本不能解读
为 0 访问。当前探针同时将六个目标置于 CXL，而性能实验每次只改变一个，
两者上下文不同，不能直接回归拟合。探针作用是验证地址/时间匹配链路。

perf 提示内核符号权限受限；本次匹配使用数据虚拟地址，不依赖内核函数名，
无需为此进一步放开系统权限。事件属性存档显示 precise_ip=2、clockid=1、
config1=0x1f（mem-loads 别名与显式 ldlat=30 合并后的实际值），后续比较需
固定相同实际事件配置。PEBS weight 仍单独报告，不替代原 AOL。

通过 26 项测试（生命周期 2、地址匹配 1、ranked/async/demand/边界 14、
仓库 9），Python 编译及 diff 空白检查通过。

下一步：在与单对象性能干预完全一致的配置下增加地址样本覆盖，先对缺失
对象做覆盖诊断，再比较原 AOL、采样访问密度、PEBS weight、对象大小与
实际提前窗口。只有原 AOL 在有效样本上解释力不足，才引入对象条件化或
访问阶段划分，并保留原 AOL 与大小/窗口简单基线作消融对照。

## 单对象采样覆盖更新（2026-09-23）

已完成六目标各三次、每次 500 步的 Direct 单对象 PEBS 采集；18 次全部
成功，ID2/16 的零样本问题已转为可观察的低覆盖。对象背景 AOL 区间重叠，
暂不据此否定原指标。训练步数与旧计时对照不同，不直接跨实验拟合收益。
详见 [单对象 PEBS 覆盖报告](SINGLE_PEBS_REPORT.md)。
