# CPU 训练与 SOAR/ALTO：第一阶段实验

2026-09-21：已完成小型 MLP 的 saved-tensor 正确性、DRAM/CXL 放置和同步迁移验证。
这是 `docs/CPU_TRAIN_CXL_PLAN.md` 的基础步骤，尚未实现 SOAR 评分适配、
ALTO 在线协调或异步预取，不代表融合系统已经完成。

## 运行

从 SoarAlto 仓库根目录执行。依赖安装在项目内，不修改系统 Python：

```bash
python3 -m pip install --target .deps/training-python --no-cache-dir \
  torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
python3 research/cpu_training/observe_saved_tensors.py results/cpu-training-new --numa
```

输出目录必须不存在，避免覆盖证据。省略 `--numa` 只运行原生、观察和复制对照。
需要 Linux 的 `libnuma.so.1`；本机普通用户已成功执行 mbind 和自身页面的
move_pages，不需要 sudo。其他主机可能受 syscall/NUMA 权限限制，失败会报错退出，
不能将未完成目录当成成功结果。成功标志是退出码 0 且生成 `correctness.json`。

当前固定 CPU 0–7、8 intra-op/1 inter-op 线程、DRAM 节点 0、CXL 节点 2。
模型为 Linear(512,1024) → GELU → LayerNorm(1024) → Linear(1024,256)，
batch=256、FP32、SGD、3 步、固定输入和随机种子。修改硬件或工作负载前需调整这些参数。
本机 venv 因缺少 ensurepip 不可用，因此使用项目内 target 安装。
PyTorch 提示未安装 NumPy；当前实验不调用 NumPy，已正常完成。

## 已完成结果

最终本轮结果：`results/cpu-training-numa-v3/`。

| 模式 | 行为 | 相对原生最大绝对误差 |
| --- | --- | ---: |
| native | 原生 PyTorch | 参考 |
| observe | hooks 观察，保留原 storage | 0 |
| clone | 候选 saved tensors 克隆 | 0 |
| dram | 独立 mmap 副本绑定 DRAM 0 | 0 |
| cxl | 独立 mmap 副本绑定 CXL 2，直接用于 backward | 0 |
| prefetch_sync | 副本初始在 CXL 2，unpack 时同步迁到 DRAM 0 | 0 |

每种模式对比三步 loss、全部参数梯度和更新后参数，并检查有限值。
非原生模式各记录 30 次保存，其中 21 次候选、9 次参数保存。
每种受管策略对 21 个 buffer 做初始和 unpack 时逐页检查：累计各 2,886 页次，
DRAM 为 0→0，CXL 直接访问为 2→2，同步迁移为 2→0，全部满足预期。
页次是跨 buffer/迭代累加，不能解释为同时驻留量或唯一物理页数量。
每种受管策略的 30 次 pack 都有 release；图释放后 Packed、Buffer、
受管 tensor 和 mmap 的弱引用存活数均为 0。

结果文件：

- `correctness.json`：数值一致性、覆盖和对象释放检查。
- `*-events.jsonl`：保存、使用、释放、阶段时间、地址和逐页驻留汇总。
- `residency-summary.json`：本轮从日志汇总的节点页次检查。
- `manifest.json`、`torch-config.txt`、`requirements-resolved.txt`：环境与依赖。
- 两个 `.py` 快照：与 manifest 的 SHA256 一致，保留实际运行版本。

早期 `cpu-training-observe-v1`、`cpu-training-numa-v1/v2` 保留作开发记录。
v2 发现单纯用地址去重会将 allocator 地址重用误认成共享 storage，已改为
检查仍存活的 storage 对象；v3 三种受管策略的候选数量一致。

## 边界与兼容调整

本轮未修改全局参数：numa_balancing=1、pte_scale=16、perf_event_paranoid=4、
demotion=false，内核为 6.8.12-138-soaralto。相对原计划的“关闭 balancing”，
本轮使用独立 mmap 的 MPOL_BIND，并在初始和 unpack 查询验证位置，避免改动
服务器全局设置。查询只证明这些时间点的位置，不能推导期间没有迁移。
后续性能因果对照仍需按计划控制 balancing 模式。

仅管理所测 MLP 中完整、连续 storage 的数值副本；排除参数、非连续、部分
storage 和可检测的重复保存。此筛选不是完整别名/原地修改分析器，不宣称支持
任意网络、共享 view、多次 backward、稀疏 tensor 或 checkpointing。
不得迁移任意 PyTorch allocator 堆页。

这里的 CXL 放置是 pack 时直接复制到 CXL，不是先将受管副本放 DRAM 再降级；
`prefetch_sync` 实际是需求时同步迁回，没有提前量与计算重叠。
尚未计量容量收益，副本可能增加峰值内存；原始参数、输入等未统一绑定节点。
工作集小、日志和查询开销大，步耗时仅用于诊断，不能用来声称训练加速或比较策略。

## 下一步仍围绕 SOAR/ALTO

1. 扩展 tensor registry 的生命周期、算子标签和地址代际，验证别名、重复 unpack
   与原地修改边界；PEBS 地址关联应使用实际 buffer 生命周期，不能只用裸地址。
2. 单独采集 PEBS/区间 PMU，复用 `src/soar/run/proc_obj_e.py` 和
   `profile_intervals.py` 计算评分；perf 权限不足时使用临时 sudo 包装并恢复设置。
3. 受控算子/单张量放置干预，验证 SOAR 排序与实测快层收益是否相关；扩大到超过 LLC。
4. 验证 MPOL_BIND→MPOL_DEFAULT 的权限交接，再接入
   `run/bc-urand/set_scan_scale.py::decision`，对照固定 16 与动态 ALTO。
   当前不应启用全局 ALTO 控制来给绑定 buffer 的实验贴上协同标签。

实现入口：`observe_saved_tensors.py` 为 workload/hooks/一致性校验，
`numa_buffer.py` 为独立 mmap、放置与逐页迁移。原 SOAR/ALTO 核心代码未因本轮改动。
