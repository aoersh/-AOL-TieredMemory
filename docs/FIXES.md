# SoarAlto 复现修复记录

本文档记录原始 SOAR/ALTO 复现中的兼容修复和证据。当前 CPU 训练研究请以 [v3 实验方案](CPU_TRAIN_CXL_PLAN.md) 为准。

## 已完成修复

### 时钟和区间

perf 统计、分配生命周期和采样使用统一的 `CLOCK_MONOTONIC` 参考。分析器按半开区间处理边界，保留短的最后区间，避免把样本错误摊到整个对象生命周期。

### PMU 和事件

针对 Granite Rapids model 173 保存独立事件配置，检查 `time_enabled/time_running`、复用和未支持事件。事件缺失时报告 N/A，不用缩放值冒充精确计数。

### 分配和预算

SOAR/hotness 采用明确的字节预算，拦截器跟踪扩容、释放和并发分配。支持范围外的 allocator 路径不宣称已验证；测试覆盖预算、峰值和释放后的 live bytes。

### NBT/ALTO

NBT/ALTO 补丁已移植到 Linux 6.8，候选内核在隔离 VM 中启动测试。`pte_scale=0` 可禁止扫描，`pte_scale=16` 可启用扫描。物理机上已完成有限的 native NBT 和在线 ALTO 运行，但不能把离线回放称为在线控制。

## 当前测试

```bash
python3 -m unittest discover -s tests -v
```

当前仓库回归测试覆盖 allocator、ALTO 决策、采样区间、主机安装顺序和分析器边界。CPU 训练额外测试位于 `research/cpu_training/test_*.py`。

## 尚未解决或不在本范围

- TPP、Nomad、Colloid 的全部内核集成不是 NBT 修复自动解决的。
- SOAR score 不是时间收益；需要在新硬件和 DNN tensor 上重新验证。
- 当前异步预取 FIFO 存在需求顺序倒置，尚未形成强 Always-Prefetch 基线。
- 旧实验的有限重复不能作为论文性能结论；后续必须使用独立进程和成对统计。
