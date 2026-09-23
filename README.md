# -AOL-TieredMemory

本仓库基于论文 **Tiered Memory Management Beyond Hotness**，包含 SOAR 对象分配和 ALTO 页面迁移实现，并加入 CPU DNN 训练与 CXL 内存的研究实验。

## 当前研究方向

当前 v3 方案先验证不同训练张量在 **Direct CXL Access** 与 **Prefetch → DRAM** 之间是否存在稳定的最优选择，再比较访问频率、AOL 和 SOAR Performance Criticality 的解释能力，最后实现简单的收益感知选择性预取。ALTO 在线控制属于后续可选扩展。

当前已完成保存张量的数值正确性、DRAM/CXL 放置、同步迁移，以及 FIFO 异步迁移的初步实验。FIFO 预取存在需求顺序倒置，尚不能作为优化后的 Always-Prefetch 基线。详见 [E1/E2 实验报告](research/cpu_training/E1_E2_REPORT.md)。

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
