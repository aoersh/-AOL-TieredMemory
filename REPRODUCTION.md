# SoarAlto 复现与研究运行说明

本文档记录当前服务器上的 SOAR/ALTO 复现、CXL 环境和 CPU 训练研究状态。原始论文实验、服务器兼容修复和新研究实验分开统计。

## 当前状态

- 内核：`6.8.12-138-soaralto`，物理机已启动并可运行 NBT/ALTO 相关实验。
- CPU：双路 Xeon 6515P Granite Rapids，64 个逻辑 CPU。
- NUMA：DRAM 节点 0/1，各约 64 GiB；CXL RAM 节点 2/3，各约 64 GiB。
- CXL：`sudo cxl list -M` 可看到 `mem0`、`mem1`，各 `ram_size=68719476736`；DAX region 分别指向节点 2/3。
- 初始复现阶段记录参数（历史值）：`numa_balancing=1`、`pte_scale=16`、`perf_event_paranoid=4`、`demotion=false`。每次实验仍需重新读取。
- CPU 训练 E0/E1/E2 已完成正确性和初步访问路径实验，但尚未完成收益感知策略或 AOL 预测验证。

## 编译与测试

从 `SoarAlto/` 根目录运行：

```bash
bash run/setup_reproduction.sh
python3 -m unittest discover -s tests -v
python3 -m py_compile research/cpu_training/*.py
```

构建候选内核或模块不会自动安装、修改 GRUB 或重启：

```bash
bash run/prepare_build_dependencies.sh --with-vm
bash run/build_alto_kernel.sh
bash run/test_alto_vm.sh
```

主机内核和 NVIDIA 模块说明见 [docs/HOST_ALTO_KERNEL.md](docs/HOST_ALTO_KERNEL.md)。

## 原始 SOAR/ALTO 实验

```bash
python3 run/reproduce.py --output results/new-run --workload micro --repeats 3
```

具体参数、perf 事件和预算以脚本帮助为准。GNR 原始事件只适用于 model 173；事件不可用时不能伪造 PMU 结果。原始 NBT/ALTO 的结果目录和修复范围见 [docs/FIXES.md](docs/FIXES.md)。

## CPU 训练研究

研究主线是：Direct CXL 与 Prefetch → DRAM 的选择空间 → hotness/AOL/SOAR 预测 → 简单收益策略。ALTO 在线控制属于后续可选扩展。

已完成：

- MLP 和两档 Transformer 的 native、observe、clone、受管 DRAM、Direct CXL、同步迁回正确性检查，最大误差为 0。
- 异步后台迁移的异常传播、重复 unpack、在途生命周期和释放检查。
- 两档 Transformer 的 40 个初步计时进程。当前 FIFO 预取存在需求顺序倒置，不能当作优化后的 Always-Prefetch。

入口和结果：

- [方案](docs/CPU_TRAIN_CXL_PLAN.md)
- [训练说明](research/cpu_training/README.md)
- [E1/E2 报告](research/cpu_training/E1_E2_REPORT.md)
- [归档证据](research/cpu_training/evidence/README.md)

## 系统复现限制

旧记录中的 `perf_event_paranoid=-1`、THP 和 NUMA 参数只代表当时实验，不是当前默认配置。不要把受管 saved tensor 实验称为整个进程 DRAM-only/CXL-only，也不要把诊断计时称为加速结论。每次运行保存拓扑、内核、参数、源码 hash、stdout/stderr 和完整失败记录。

## 结果文件

常见文件包括 `manifest.json`、`correctness.json`、`steps.json`、`tensor-events.jsonl`、`migration.csv`、`residency.csv`、`summary.json`、原始 perf 日志和图表。结果目录必须使用新路径，不覆盖历史证据。

## 后续入口

截至 2026-09-29，需求顺序、单对象干预和初步 PMU/PEBS 采集已完成。迁移参数向量化已验证能降低构造成本，但尚无稳定端到端加速。固定延迟时机对照已完成，单纯启动偏移不足以解释差异。C 内计时和独立/并发调用对照已完成；sudo 内核 CPU 热点采样及线程归属核验已完成，LRU 自旋锁是两组跨节点 worker 最大单项热点；下一步用缓冲区复用/分配压力干预量化其影响，再评估张量收益及 AOL 解释能力。详见最新报告及 v3 方案中的继续/停止条件。

2026-09-22 更新：上一轮需求顺序的反向边界提交已通过正确性验证，但 50 次
独立计时未显示稳定加速。下一步检查提前量、就绪任务优先级及分配/复制成本，
再做单对象因果干预；结果见 [需求顺序报告](research/cpu_training/DEMAND_ORDER_REPORT.md)。

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
