# SoarAlto 复现与研究运行说明

本文档记录当前服务器上的 SOAR/ALTO 复现、CXL 环境和 CPU 训练研究状态。原始论文实验、服务器兼容修复和新研究实验分开统计。

## 当前状态

- 内核：`6.8.12-138-soaralto`，物理机已启动并可运行 NBT/ALTO 相关实验。
- CPU：双路 Xeon 6515P Granite Rapids，64 个逻辑 CPU。
- NUMA：DRAM 节点 0/1，各约 64 GiB；CXL RAM 节点 2/3，各约 64 GiB。
- CXL：`sudo cxl list -M` 可看到 `mem0`、`mem1`，各 `ram_size=68719476736`；DAX region 分别指向节点 2/3。
- 当前记录参数：`numa_balancing=1`、`pte_scale=16`、`perf_event_paranoid=4`、`demotion=false`。每次实验仍需重新读取。
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

下一步先修复异步预取的需求顺序调度，解释 Direct CXL 与受管 DRAM 的差异，再做单 tensor 因果干预；之后才采集 PMU/PEBS 并评估 AOL。详见 v3 方案中的继续/停止条件。
