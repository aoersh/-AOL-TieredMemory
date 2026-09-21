# -AOL-TieredMemory

本仓库基于论文 **Tiered Memory Management Beyond Hotness**，包含 SOAR 对象分配和 ALTO 页面迁移实现，并加入 CPU DNN 训练与 CXL 内存的研究实验。

## 当前研究方向

当前 v3 方案先验证不同训练张量在 **Direct CXL Access** 与 **Prefetch → DRAM** 之间是否存在稳定的最优选择，再比较访问频率、AOL 和 SOAR Performance Criticality 的解释能力，最后实现简单的收益感知选择性预取。ALTO 在线控制属于后续可选扩展。

当前已完成保存张量的数值正确性、DRAM/CXL 放置、同步迁移，以及 FIFO 异步迁移的初步实验。FIFO 预取存在需求顺序倒置，尚不能作为优化后的 Always-Prefetch 基线。详见 [E1/E2 实验报告](research/cpu_training/E1_E2_REPORT.md)。

- [编译与复现说明](REPRODUCTION.md)
- [CPU 训练 CXL 实验方案](docs/CPU_TRAIN_CXL_PLAN.md)
- [训练实验入口与结果说明](research/cpu_training/README.md)
- [归档实验数据](research/cpu_training/evidence/README.md)

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
