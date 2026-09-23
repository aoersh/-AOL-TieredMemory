# CPU 训练：收益感知 CXL 访问与选择性预取实验

2026-09-21：已完成 MLP 和两档小型 Transformer 的 saved-tensor 正确性、
DRAM/CXL 放置和同步迁移验证。
这是 [v3 实验方案](../../docs/CPU_TRAIN_CXL_PLAN.md) 的基础步骤，尚未实现 SOAR 评分适配、
ALTO 在线协调或收益策略。已新增简单异步预取与首轮计时，但发现 FIFO 需求顺序
倒置，仍需优化强预取基线，详见 [E1/E2 报告](E1_E2_REPORT.md)。
2026-09-22 新增上一轮需求顺序调度，完成 10 次诊断和 50 次计时：顺序正确，
但整步未稳定加速，详见 [需求顺序实验报告](DEMAND_ORDER_REPORT.md)。
2026-09-22 新增 pack-time 历史优先级队列：大规模相对 FIFO 平均减少约
8.9 ms/step，小规模差异区间跨 0；两档仍明显慢于 Direct CXL，详见
[优先级预取报告](READY_PRIORITY_REPORT.md)。
随后完成单 saved tensor 隔离实验：只将 ID 2、6、10 放在 CXL，其余候选在
DRAM；有效结果和一次已作废的隔离器错误说明见 [单 tensor 报告](SINGLE_TENSOR_REPORT.md)。
随后完成 1/3/6 个目标 tensor 的多对象实验；迁移量增加时 ranked 开销增加，
但尚未施加 DRAM 上限，不能解释为容量收益，见 [多对象报告](GROUP_PRESSURE_REPORT.md)。
随后完成受管 tensor 池预算矩阵：Direct、budget 0/1/2 MiB 共 20 个独立进程，
预算 1/2 MiB 每步分别迁移约 1/2 MiB 并增加 step 开销；预算 0 与 Direct 基本
重合。该预算是受管池代理，不是整进程 DRAM 上限，详见 [预算报告](BUDGET_REPORT.md)。
随后在用户临时开放 `perf_event_paranoid=-1` 后完成 Direct、budget 1 MiB、
ranked 各 5 次 PMU 窗口采集；事件 running=100%，但目前仍是整进程窗口，尚未
完成 tensor 级 AOL 归因，详见 [PMU 报告](PMU_REPORT.md)。
v3 方案已改为先验证 Direct CXL / Prefetch 的收益
差异，再验证 AOL/Performance Criticality，最后设计选择性预取；ALTO 在线
联动作为可选扩展。以下已完成实验及原始结果保持不变。

## 运行

从 SoarAlto 仓库根目录执行。依赖安装在项目内，不修改系统 Python：

```bash
python3 -m pip install --target .deps/training-python --no-cache-dir \
  torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install --target .deps/training-python --no-cache-dir numpy==1.26.4
python3 research/cpu_training/observe_saved_tensors.py results/cpu-training-new --numa
```

输出目录必须不存在，避免覆盖证据。省略 `--numa` 只运行原生、观察和复制对照。
需要 Linux 的 `libnuma.so.1`；本机普通用户已成功执行 mbind 和自身页面的
move_pages，不需要 sudo。其他主机可能受 syscall/NUMA 权限限制，失败会报错退出，
不能将未完成目录当成成功结果。成功标志是退出码 0 且生成 `correctness.json`。

默认 CPU 0–7、8 intra-op/1 inter-op 线程、DRAM 节点 0、CXL 节点 2；
新增参数可调整 CPU 列表、线程、节点、训练步数、模式及 workload/shape。
模型为 Linear(512,1024) → GELU → LayerNorm(1024) → Linear(1024,256)，
batch=256、FP32、SGD、3 步、固定输入和随机种子。修改硬件或工作负载前需调整这些参数。
本机 venv 因缺少 ensurepip 不可用，因此使用项目内 target 安装。
旧 pilot 曾提示未安装 NumPy，现已在项目内补齐 1.26.4；严格导入和
Tensor/NumPy 双向转换检查通过，不需要修改系统 Python。

## v3 方案 E0 进展（2026-09-21）

结果：`results/cpu-training-e0-validated/`，汇总为该目录 `summary.json`。
本次实现参数化和显式 matmul/softmax 的双层 Transformer Encoder，
无 dropout、无原地激活、无融合 attention，FP32 + SGD + 合成输入。
每组 native/observe/clone/dram/cxl/prefetch_sync 六种模式，各三步：

| 工作负载 | 配置 | 最大绝对误差 | 每种受管模式累计候选保存数 | 受管对象峰值代理 |
| --- | --- | ---: | ---: | ---: |
| MLP | 原始配置，batch 256 | 0 | 21 | 3.758 MiB |
| Transformer small | batch 4，seq 128，width 128，heads 4，layers 2 | 0 | 105 | 10.789 MiB |
| Transformer large | batch 8，seq 512，其余相同 | 0 | 105 | 134.156 MiB |

峰值代理按页对齐 buffer 从初始驻留检查至 Packed 释放统计，不是精确 mmap
存活时间或进程 RSS。大配置超过单插槽 72 MiB LLC，但不据此断言 LLC miss
或内存瓶颈。候选逻辑保存字节占所有保存字节的比例分别约 78.8%、75.1%、
67.2%；不是全进程管理覆盖率或唯一 storage 比例。

所有模式的候选 ID/shape/dtype/排除原因序列一致。三组逐页初始和 unpack
检查分别累计 2,886、8,286、103,032 页次/受管模式，节点符合 0→0、2→2、
2→0。loss/梯度/更新后参数一致且有限，图释放后受管对象/映射全部释放。
各结果目录含源码快照、依赖、manifest、事件日志和 `audit-summary.json`；
根目录保留执行命令、stdout/stderr 和六项边界测试日志。

```bash
# 保留旧 MLP 入口；用新目录，避免覆盖结果。
python3 research/cpu_training/observe_saved_tensors.py results/e0-mlp-new --numa
python3 research/cpu_training/observe_saved_tensors.py results/e0-transformer-new \
  --workload transformer --batch 8 --sequence 512 --width 128 --heads 4 \
  --layers 2 --steps 3 --threads 8 --numa
python3 research/cpu_training/test_boundaries.py
python3 research/cpu_training/summarize_correctness.py results/e0-transformer-new
```

边界检查包括非连续/部分 view、重复 storage、地址代际判别、仍存活源对象的
原地修改拒绝、重复 unpack 的梯度/单次迁移/释放、注入迁移失败的传播。
原地修改检测仅检查仍存活原 tensor wrapper 的版本，不是通用别名分析；
仍限定于当前非原地 workload。完整 storage 别名组还未统一管理。

已自行解决的事项：补齐 NumPy；重复 unpack 不再重复提交已完成的同步迁移；
失败时保存 traceback；保存三个实现文件的哈希/快照；运行前写 manifest。
此前普通用户 `cxl list -M` 的枚举错误由用户 sudo 成功确认，sysfs 验证
两个 64 GiB RAM region 的 DAX target node 为 2/3。本次全局参数保持
numa_balancing=1、pte_scale=16、perf_event_paranoid=4、demotion=false。

上述 E0 没有运行性能重复实验，旧入口计时仍含诊断、梯度/参数复制和验证开销，
不能比较策略速度。后续新增入口已分离低开销计时，完成异步工作者及异常安全
检查和 40 个进程的初步计时；结论与调度限制见 E1/E2 报告。完整别名支持仍有限。

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

## 下一步：先验证访问路径选择空间

1. 参数化、正确性、计时拆分和后台执行已完成。按上一轮需求顺序提交已验证，
   但反向边界提交失去前向窗口；下一步结合就绪优先级与提前量验证，而非认定
   排序正确就足够。补充分配/复制分解和少量单对象干预，继续完善别名边界。
2. 分解 Direct 与受管 DRAM 的性能差异；强基线验证后再做单对象干预，
   独立确认两种访问路径的收益边界，不用当前全量 FIFO 结果代替 Q1 验证。
3. 在问题验证基础上关联 PEBS/区间 PMU，复用 `src/soar/run/proc_obj_e.py` 和
   `profile_intervals.py`，比较 hotness、AOL、SOAR score 与实测净收益。
   权限不足时再准备临时 sudo 包装；PMU 不可用不阻塞直接计时实验。
4. 只有选择空间得到验证后才实现简单收益策略。ALTO 保护/解除、固定扫描与
   动态扫描作为后续可选对照，不再作为首轮研究成立的强制条件。

实现入口：`observe_saved_tensors.py` 为参数/hooks/一致性校验，
`workloads.py` 为 MLP 和显式 attention Transformer，`numa_buffer.py` 为
独立 mmap、放置与逐页迁移，`test_boundaries.py` 与 `summarize_correctness.py`
为边界和日志检查。原 SOAR/ALTO 核心代码未因本轮改动。

## 2026-09-23 测量审计更新

修复生命周期仅保留最后一步及首次 unpack 被误当释放边界的问题；此前
共享窗口 AOL 结果不能用于否定 AOL。完成六对象 18 次正确性及 90 次计时，
尚未发现稳定预取赢家；PEBS 首次获得 266 个正式步骤受管地址匹配样本，
仍不足以做对象相关性结论。权限当前为用户开放的 -1。详见
[测量审计与新结果](MEASUREMENT_AUDIT_0923.md)。

## 单对象采样覆盖更新（2026-09-23）

已完成六目标各三次、每次 500 步的 Direct 单对象 PEBS 采集；18 次全部
成功，ID2/16 的零样本问题已转为可观察的低覆盖。对象背景 AOL 区间重叠，
暂不据此否定原指标。训练步数与旧计时对照不同，不直接跨实验拟合收益。
详见 [单对象 PEBS 覆盖报告](SINGLE_PEBS_REPORT.md)。

## 500 步阶段对齐结果（2026-09-23）

完成六对象 Direct/ranked 各三次计时（36 进程），与独立 PEBS 特征的
完整 505 步 loss 和源码一致。平均预取净收益均为负，尚无稳定预取赢家；
六对象描述性相关性不足以否定 AOL。详见 [对齐实验报告](MATCHED_500_REPORT.md)。

## 预取原因验证的初步结论（2026-09-23）

完成两档三方对照共 63 次计时：未发现稳定初始 DRAM 放置优势；大配置
32 MiB 目标的 ranked 比 Direct 慢约 32.4 ms/step，约 29.0 ms 出现在
forward，需求处等待仅约 0.046 ms。预取流程额外成本是当前主要问题，
具体的软件/迁移/缓存机制尚未分离，不据此否定 AOL。见
[三方对照与初步结论](PLACEMENT_CAUSE_REPORT.md)。
