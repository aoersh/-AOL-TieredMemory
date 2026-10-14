# -AOL-TieredMemory

本仓库基于论文 **Tiered Memory Management Beyond Hotness**，包含 SOAR 对象分配和 ALTO 页面迁移实现，并加入 CPU DNN 训练与 CXL 内存的研究实验。

## 当前研究方向

2026-10-14完成[全面科研审计](research/cpu_training/RESEARCH_AUDIT_2026-10-14.md)，
梳理研究时间线、假设树、模块取舍、证据边界和停止条件。当前证据尚不足形成
完整论文贡献；收窄问题为：**同一DRAM预算下，哪些训练storage值得占用快内存，
什么时候应保留Direct CXL？**

停止当前ID10/ID34 attention迁移调参及高开销C10 mmap分配器上的性能搜索，
暂停此前拟议的softmax backward输入梯度/输出位置小控制；AOL、复杂ML及
SOAR/ALTO强制集成暂不推进。保留原生基线、公平负例、正确性/驻留审计和
迁移与前台分配干扰线索，历史代码与原始数据继续保存。

下一阶段建议用7–10个工作日依次验证文献差异、低开销容量执行和同预算静态
对象分配；只有确认可重复端到端收益，才进入动态迁移或AOL。该计划尚未实施，
本次审计未新增实验。当前断点见[交接说明](research/cpu_training/HANDOFF.md)。

## 已完成阶段与历史方向

以下记录保留各阶段结果及当时设想；下一步以审计和最新交接为准。

2026-10-14继续完成原生阶段缺页/算子分析：6个CPU profiler诊断进程与10个
无采样独立计时进程。无采样DRAM/CXL为76.803/90.470 ms，差+13.667 ms
[4.869,22.465]，再次确认整体放置损失。反向五轮均慢，四轮CXL缺页反而更少；
反向bmm、softmax backward及mm是采样候选，尚非因果分解。当时拟议的输入
梯度/临时输出位置与首次写入小控制已在科研审计后暂停。
见 [阶段报告](research/cpu_training/NATIVE_OPERATOR_REPORT.md) 和
[轻量证据](research/cpu_training/evidence/native-operator-1014/README.md)。

2026-10-14补齐默认分配器原生DRAM/CXL对照：两个诊断、10个独立计时进程，
无保存副本或迁移。DRAM/CXL为74.245/89.284 ms/步，差+15.039 ms
[6.046,24.032]，五轮方向一致但幅度有波动。确认当前训练对整体匿名放置敏感，
尚未证明动态迁移收益或有限DRAM策略。新C10分配器暂留容量/正确性原型，
后续优先默认路径的算子/阶段归因，池化不是研究前提。见
[原生放置报告](research/cpu_training/NATIVE_PLACEMENT_REPORT.md)、
[阶段总结](research/cpu_training/DAILY_SUMMARY_2026-10-14.md) 和
[轻量证据](research/cpu_training/evidence/native-placement-1014/README.md)。

2026-10-13完成原生框架校准与无保存副本的C10张量容量筛查：36个独立计时进程、
10个诊断进程。旧副本框架相对原生有显著成本；新分配器虽覆盖参数/梯度/临时
张量并守住64/128 MiB预约配额，但自身开销显著，尚无稳定放置赢家。不是整进程
DRAM容量上限，暂不推进迁移/AOL，先解决分配器成本及账本外内存。
见 [本轮报告](research/cpu_training/TENSOR_CAPACITY_REPORT.md) 与
[今天总结](research/cpu_training/DAILY_SUMMARY_2026-10-13.md)。

最新收敛验证（2026-10-12）：完成真实SoftmaxBackward重放30个独立计时进程，
以及sequence1024完整训练10个独立进程。128 MiB副本初始DRAM创建更快约3.04 ms，
但完整训练为Direct284.38/DRAM放置284.56 ms；差值区间[−2.11,2.47] ms落在±3%
门槛内，没有实用加速。本阶段停止当前两层attention对象的迁移与放置搜索，
暂不引入AOL收益模型或复杂ML。详见
[真实backward与完整训练确认](research/cpu_training/BACKWARD_REPLAY_REPORT.md)。

这里的 Direct/初始DRAM 都经过受管保存副本复制；不是整进程CXL与无额外复制的
原生DRAM训练比较，也没有全进程DRAM容量限制。已测迁移负收益不能推广到所有
CPU训练或AOL。后续优先验证有实际依据的容量不足场景，先静态放置、再动态迁移、
最后检验AOL增量价值；各阶段需通过完整训练收益门槛。

9月21日的 v3 历史方案先验证不同训练张量在 **Direct CXL Access** 与 **Prefetch → DRAM** 之间是否存在稳定的最优选择，再比较访问频率、AOL 和 SOAR Performance Criticality 的解释能力，最后实现简单的收益感知选择性预取。该排期已由上述科研审计路线取代。

当前已完成保存张量的数值正确性、DRAM/CXL 放置、同步迁移，以及 FIFO 异步迁移的初步实验。FIFO 预取存在需求顺序倒置，尚不能作为优化后的 Always-Prefetch 基线。详见 [E1/E2 实验报告](research/cpu_training/E1_E2_REPORT.md)。

2026-10-09 已完成修正后的独立分配压力 2×2、Direct CXL/初始 DRAM/Prefetch 公平三方对照，以及目标 saved tensor 创建时间与缺页分段测量。复用非目标 DRAM 副本会降低前台迁移调用成本，但完整运行的交互差异区间跨零；Prefetch 仍比 Direct CXL 慢，当前没有稳定的预取赢家。早期不公平的 10 月 8 日结果已列入排除清单，不能作为结论。详见 [收敛实验报告](research/cpu_training/CLOSURE_REPORT_1009.md)、[当天总结](research/cpu_training/DAILY_SUMMARY_2026-10-09.md) 和 [轻量证据](research/cpu_training/evidence/closure-1009/README.md)。

2026-09-22 已新增按上一轮需求顺序调度的反向边界预取，并完成 50 次独立计时。
提交顺序正确，但整步未稳定改善；研究假设尚未验证，见
[需求顺序实验报告](research/cpu_training/DEMAND_ORDER_REPORT.md)。

最新单 tensor 实验只将三个目标对象置于 CXL，其余候选保持 DRAM；ranked 接近
但未稳定胜过 Direct。AOL 仍受 perf 权限限制，见
[单 tensor 报告](research/cpu_training/SINGLE_TENSOR_REPORT.md)。
多对象实验显示没有 DRAM 预算时预取管理成本随目标数量增加；真正的容量压力
实验尚未开始，见 [多对象报告](research/cpu_training/GROUP_PRESSURE_REPORT.md)。

- [编译与复现说明](REPRODUCTION.md)
- [CPU 训练 CXL 实验方案](docs/CPU_TRAIN_CXL_PLAN.md)
- [训练实验入口与结果说明](research/cpu_training/README.md)
- [归档实验数据](research/cpu_training/evidence/README.md)

## 2026-10-12：有限 DRAM 与对象选择阶段实验

完成受管 saved-tensor 副本池的 32/64 MiB 容量矩阵，并新增固定 32 MiB、仅选择
两层 attention 的 ID10/ID34 对照。每矩阵五条件、各三次独立进程，先验证数值、
驻留和实际释放账本，再随机串行计时。按保存顺序初始填充 DRAM 未显示稳定收益；
新对象矩阵中需求迁移任一 32 MiB 对象都慢约 30 ms/步，初始放置差异区间跨零。
这支持把“不迁移”作为有效选择，尚未验证出值得迁移的正收益边界。

额度限制的是全部合格 saved-tensor 副本，参数、输入、梯度等仍在范围外；
不能称为整进程 DRAM 容量限制。随后真实 backward 与完整训练确认已完成，
当前对象搜索停止，详见下节；不提前引入复杂 ML。
详见 [容量与对象选择报告](research/cpu_training/CAPACITY_PILOT_REPORT.md)、
[阶段总结](research/cpu_training/DAILY_SUMMARY_2026-10-12.md) 和
[对象选择证据](research/cpu_training/evidence/capacity-selection-1012/README.md)。

## 2026-10-12：真实 backward 与完整训练收敛验证

新增捕获真实保存值及梯度的 SoftmaxBackward 单次重放，三个案例、两节点、各五个
独立计时进程，另六组诊断；随后只追加一组sequence1024完整训练，两个条件各五个
进程，另两组诊断。局部128 MiB副本的DRAM创建更快约3.045 ms，但整步没有实用
加速，停止当前两层attention的迁移和放置调参。38项回归及九项审计故障注入通过。

入口位于 `research/cpu_training/`，轻量证据按 `diagnostic/timing/source/validation`
归档；完整原始步骤、日志和二进制保留在本机 `results/`。
详见 [实验报告](research/cpu_training/BACKWARD_REPLAY_REPORT.md)、
[今天的总结](research/cpu_training/DAILY_SUMMARY_2026-10-12.md)、
[重放证据](research/cpu_training/evidence/backward-replay-1012/README.md) 与
[完整训练证据](research/cpu_training/evidence/large-placement-1012/README.md)。

## 目录

- `src/alto/`：ALTO 内核补丁和运行组件，支持 TPP、NBT、Nomad、Colloid。
- `src/soar/`：SOAR profiling、评分、对象分配和放置拦截器。
- `src/microbenchmark/`：指针追踪和顺序访问微基准。
- `run/`：构建、运行、采集和分析脚本。
- `research/cpu_training/`：CPU 训练、受管张量、CXL 访问路径和预取实验。
- `tests/`：回归测试和迁移探针。

## 原始系统实验

`run/run.sh` 的系统类型沿用上游编号：

```text
0 NoTier   1 TPP       2 NBT        3 Nomad
4 Colloid  5 TPP-ALTO 6 NBT-ALTO   7 Nomad-ALTO
8 Colloid-ALTO  9 Local  10 Remote  11 SOAR
```

完整命令和本机兼容修改见 [REPRODUCTION.md](REPRODUCTION.md)。原始 SOAR/ALTO 论文实验与本研究的 CPU 训练实验分开记录，不能混用性能结论。

## 论文与许可证

原始论文：*Tiered Memory Management Beyond Hotness*，OSDI 2025。代码使用 MIT License，原始引用信息保留在仓库历史和上游项目中。

## 2026-09-22 受管池预算实验更新

完成 Direct 与 budget 0/1/2 MiB 的 20 个独立进程计时。预算迁移量符合限制，
但当前预取未优于 Direct；该上限仅限制指定 saved tensor 的每步累计迁入，
不是整进程 DRAM 限额。详见 [预算实验报告](research/cpu_training/BUDGET_REPORT.md)。
PMU 仍被 `perf_event_paranoid=4` 拒绝，尚无本轮 AOL 结果。

## 2026-09-23 测量审计更新

修复生命周期仅保留最后一步及首次 unpack 被误当释放边界的问题；此前
共享窗口 AOL 结果不能用于否定 AOL。完成六对象 18 次正确性及 90 次计时，
尚未发现稳定预取赢家；PEBS 首次获得 266 个正式步骤受管地址匹配样本，
仍不足以做对象相关性结论。权限当前为用户开放的 -1。详见
[测量审计与新结果](research/cpu_training/MEASUREMENT_AUDIT_0923.md)。

## 单对象采样覆盖更新（2026-09-23）

已完成六目标各三次、每次 500 步的 Direct 单对象 PEBS 采集；18 次全部
成功，ID2/16 的零样本问题已转为可观察的低覆盖。对象背景 AOL 区间重叠，
暂不据此否定原指标。训练步数与旧计时对照不同，不直接跨实验拟合收益。
详见 [单对象 PEBS 覆盖报告](research/cpu_training/SINGLE_PEBS_REPORT.md)。

## 500 步阶段对齐结果（2026-09-23）

完成六对象 Direct/ranked 各三次计时（36 进程），与独立 PEBS 特征的
完整 505 步 loss 和源码一致。平均预取净收益均为负，尚无稳定预取赢家；
六对象描述性相关性不足以否定 AOL。详见 [对齐实验报告](research/cpu_training/MATCHED_500_REPORT.md)。

## 预取原因验证的初步结论（2026-09-23）

完成两档三方对照共 63 次计时：未发现稳定初始 DRAM 放置优势；大配置
32 MiB 目标的 ranked 比 Direct 慢约 32.4 ms/step，约 29.0 ms 出现在
forward，需求处等待仅约 0.046 ms。预取流程额外成本是当前主要问题，
具体的软件/迁移/缓存机制尚未分离，不据此否定 AOL。见
[三方对照与初步结论](research/cpu_training/PLACEMENT_CAUSE_REPORT.md)。

## 迁移成本拆分更新（2026-09-29）

完成五条件、15 个计时进程及五个正确性诊断。页面参数准备相对空任务增加约
6.77 ms/step，真实迁移路径相对准备再增加约 29.98 ms/step，两项配对区间均
高于零。空任务与 Direct 的差异区间跨零。下一步先降低参数准备成本，再分离
迁移与计算干扰；本轮不否定 AOL。详见 [迁移成本拆分报告](research/cpu_training/MIGRATION_ABLATION_REPORT.md)。

## 页面参数向量化结果（2026-09-29）

完成七条件、21 个计时进程。真实迁移参数准备从 5.776 降至 0.215 ms，
但 move_pages 墙钟增加约 5.523 ms；整步从 143.365 降至 141.307 ms，
差异区间跨零，尚未证明稳定训练加速。优化版仍慢于 Direct。下一步验证
迁移开始时机与计算重叠，暂不改造 AOL。详见 [向量化对照报告](research/cpu_training/VECTORIZED_MIGRATION_REPORT.md)。

## 固定延迟时机对照（2026-09-29）

六条件、18 个计时进程完成。向量化后固定等待 5.5 ms，使系统调用启动偏移
接近旧版（6.445 vs 6.358 ms），但调用仍慢约 4.197 ms，区间高于零；整步
未稳定改善。单纯启动时间偏移不足以解释差异。下一步比较独立/并发搬页并
记录线程 CPU 时间与计算阶段，暂不改造 AOL。详见 [时机对照报告](research/cpu_training/MIGRATION_TIMING_REPORT.md)。

## C 内计时与同节点对照（2026-09-29）

完成 18 个训练计时进程和三个独立调用进程。Python 外层额外墙钟均值小于
0.04 ms，向量化跨节点 C 内 CPU/墙钟约 99.6%，不支持 GIL 返回等待或长时间
睡眠为主要原因。同节点调用约 4.913 ms，跨节点约 50.016 ms。35 项回归通过。
随后已完成 sudo 内核热点采样，线程聚合问题已修复，结果见下一节。详见 [本轮确定结论与命令](research/cpu_training/NATIVE_MIGRATION_REPORT.md)。

## 2026-09-29：内核采样完成，定位 LRU 自旋热点

三组采样完成、丢样为零。修复 perf 默认同名线程聚合造成的 TID 归属遗漏后，
两组跨节点迁移线程的 LRU 路径自旋占内核样本权重 40.70% / 41.63%，
copy_page 为 18.78% / 16.76%；其他线程的匿名缺页/LRU 自旋也明显。
这支持优先检查迁移与前台分配干扰，但不是墙钟分解或因果收益证明。
下一步公平比较缓冲区复用/分配压力，保持初始驻留、迁移量与端到端计时。
详见 [采样修复、确定结论与下一步](research/cpu_training/KERNEL_MIGRATION_PROFILE_REPORT.md)。
