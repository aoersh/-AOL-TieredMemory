# SOAR：基于排序的静态对象分配

SOAR 使用对象访问特征和性能临界性，在分层内存中为对象选择快层或慢层。当前实现包含 profiling、评分分析和分配拦截器。

## 目录

- `prof/`：采集分配生命周期和访问信息。
- `run/`：处理 profiling 输出、计算评分和生成策略。
- `interc/`：运行时拦截分配并按策略放置对象。

## 使用流程

### 1. 性能采样（Profiling）

运行 workload，记录分配地址、大小、生命周期、PMU/PEBS 区间和时钟来源。Granite Rapids 使用 `configs/gnr-events.json`；perf 权限不足时只报告可用指标。

### 2. 分析

```bash
python3 src/soar/run/proc_obj_e.py <profile-output>
```

分析结果必须记录样本覆盖、时间边界和预算。SOAR score 是排序量，不是秒数或迁移字节数。

### 3. 分配

使用 `LD_PRELOAD` 加载拦截器，并通过 `SOAR_POLICY`、`SOAR_FAST_NODE`、`SOAR_SLOW_NODE` 和字节预算控制放置。先运行测试和小规模 workload，再扩大实验。

CPU 训练研究不会把普通 PyTorch allocator 页直接交给拦截器，而是通过 `research/cpu_training/` 的独立 buffer 验证张量生命周期，避免迁移无关对象。
