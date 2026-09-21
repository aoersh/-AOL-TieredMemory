# 运行脚本说明

## 当前研究入口

原始 SOAR/ALTO 实验说明保留在脚本中；本机复现步骤见 [REPRODUCTION.md](../REPRODUCTION.md)。CPU 训练研究请先阅读 [实验方案](../docs/CPU_TRAIN_CXL_PLAN.md) 和 [训练实验说明](../research/cpu_training/README.md)。当前优先比较 Direct CXL 与异步预取，ALTO 在线控制属于后续扩展。

## 原始运行方式

```bash
./run.sh [type] [threads] [MLC-threads-list]
```

`type` 使用 README 中的 0–11 编号，`threads` 是应用线程数，`MLC-threads-list` 是逗号分隔的 MLC 线程数。`bc-urand` 示例：

```bash
./run.sh 5 4 0,1,2   # TPP-ALTO
./run.sh 1 4 0,1,2   # TPP 基线
./run.sh 11 4 0,1,2  # SOAR
```

脚本可能涉及内核、THP、NUMA balancing、perf 和内存容量设置。执行前保存系统状态，优先使用项目中的受限脚本；不要把旧 CloudLab 的 `memmap` 参数直接用于当前服务器。

## 组件

- `calpg.sh`：统计页面提升。
- `proc_obj_e.py`：分析 SOAR 对象访问和评分。
- `run/bc-urand/set_scan_scale.py`：ALTO 扫描比例决策。
- `research/cpu_training/`：新的 CPU 训练访问路径实验。
