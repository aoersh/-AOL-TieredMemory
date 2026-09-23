# 面向 CPU 深度学习训练的收益感知 CXL 访问与选择性预取

版本：v3，2026-09-21。英文暂名：Benefit-Aware CXL Memory Access and
Selective Prefetching for CPU-based DNN Training。

本版依据新的问题定位替代 v2 的三组件强制协同路线。主线改为：先验证
Direct CXL / Prefetch 的决策空间，再验证 SOAR/ALTO 的 AOL 与 Performance
Criticality 能否帮助选择，最后实现简单收益策略。ALTO 在线扫描作为后续
可选集成和对照，不再作为第一阶段完成或研究成立的必要条件。
本文件是待执行方案，以下拟议实验不代表已经获得结果。

## 1. 三个问题与研究边界

- Q1：真实 CPU DNN 训练中，不同受管 tensor 是否存在可重复的最优访问路径差异？
- Q2：AOL / Performance Criticality 是否比访问频率更能解释并预测这种差异？
- Q3：同样的迁移执行器、预取窗口和 DRAM 预算下，简单收益策略能否减少无效
  预取并降低端到端训练时间？

不预设低 AOL、高 AOL、顺序或随机访问应选择哪条路径。不预设混合选择
一定优于 Always-Prefetch，也不将“迁移有成本”本身作为创新。

首版只管理 CPU eager-mode、FP32 训练中 autograd 保存供 backward 使用的
连续稠密 tensor 副本，决策单位为 tensor/storage group，执行粒度为 4 KiB 页。
不同时开展独立逐页预测算法。参数、梯度、优化器状态和输入不属于受管池，
但需要统计其内存占用。暂不引入 GPU、LLM、分布式、torch.compile、
checkpointing、复杂 ML 或新内核算法。

## 2. 已有基础与尚未完成的工作

已完成 SOAR/ALTO 的本机兼容和基础实验；CPU 训练试验见
[运行说明](../research/cpu_training/README.md) 和
[已归档证据](../research/cpu_training/evidence/numa-v3/)。

小型 MLP 的三步实验已完成 native、observe、clone、受管 DRAM、受管 CXL
直接访问、需求时同步迁回 DRAM。loss、梯度、更新后参数最大绝对误差均为 0；
21 次受管 buffer 保存的初始/使用时逐页位置检查通过，图释放后映射释放。

这些结果仅支持基础正确性：工作集小，带详细查询与日志，原始参数和输入未
统一绑定，没有测量容量收益，`prefetch_sync` 没有提前量或计算重叠。
截至最初 MLP pilot，尚未完成异步预取、真实网络性能对比、tensor 级 PMU 关联或收益策略。
2026-09-21 E0 进展：参数化原型、显式 attention 双层 Transformer 两档规模
和旧 MLP 回归已完成，六种模式各三步数值误差为 0，页面驻留、释放和
六项边界测试通过。结果在 `results/cpu-training-e0-validated/`；大配置受管
对象峰值代理约 134 MiB，超过单插槽 72 MiB LLC。此为新增正确性证据，
不是性能结果；低开销计时拆分与异步执行器仍未完成。
随后 E1/E2 已新增独立低开销计时和 eager FIFO 异步执行器，完成 8 次诊断和
40 次独立进程计时。两档 Direct 都优于当前 async，但发现 FIFO 与反向需求
顺序倒置；因此尚未形成强预取基线、单对象收益差异或 Q1–Q3 的肯定结论。
下一步先改进需求顺序调度并解释受管 DRAM/Direct 差异，详见
[E1/E2 结果与限制](../research/cpu_training/E1_E2_REPORT.md)。
2026-09-22 进展：新增上一轮需求顺序的反向边界预取，10 次诊断和 50 次计时
完成，最大数值误差 0。新策略顺序正确但没有稳定整步收益；两档仍为 Direct
最快，Q1–Q3 未获肯定结果。详见
[需求顺序实验报告](../research/cpu_training/DEMAND_ORDER_REPORT.md)。
随后新增 pack-time 历史优先级队列，60 次计时全部完成；大规模相对 FIFO 有
探索性改善，小规模不显著，但两档仍慢于 Direct，尚不足以证明预取收益或
选择性策略空间。详见
[优先级预取报告](../research/cpu_training/READY_PRIORITY_REPORT.md)。
单 tensor 隔离实验随后完成：三个目标各五次重复，ranked 仅接近 Direct 或小幅
落后，尚未观察到稳定的混合赢家；同步迁移状态错误已修复并重新运行。
PMU 权限仍阻塞 AOL 采集，详见
[单 tensor 报告](../research/cpu_training/SINGLE_TENSOR_REPORT.md)。
多对象扩展（1/3/6 个目标）随后完成，但无 DRAM 上限时 ranked 随迁移量增加而
变慢；下一步进入明确的 25%/50%/75% 受管池预算实验。详见
[多对象报告](../research/cpu_training/GROUP_PRESSURE_REPORT.md)。
2026-09-22 已完成受管 tensor 池 budget 0/1/2 MiB 矩阵（20 个独立进程）。
预算 1/2 MiB 的迁移量符合限制且 step 开销高于 Direct；这验证了预算代理和
迁移成本，但不等同于整进程 DRAM 容量压力。结果见
[预算报告](../research/cpu_training/BUDGET_REPORT.md)。
本轮从扩展该原型开始，不重做已经完成的 SOAR/ALTO 安装，也不将旧步耗时
用作新方案的性能结论。

## 3. 与已有工作的关系

### SOAR/ALTO

复用 SoarAlto 的性能关键性思想、SOAR 原评分实现和区间 PMU 时间对齐。
分别报告访问频率、AOL 单指标和原 SOAR 综合评分，不能将三者混称。
AOL 不是 DRAM 放置收益或迁移收益的时间单位，单独高 AOL 不保证应预取。
从静态放置收益到“给定窗口和预算下的预取净收益”需要重新验证。

先新增 tensor 适配层，不修改原评分或阈值，不直接给 PyTorch 套 malloc
拦截器并假定分配调用点就是 tensor。若硬件适配需要校准，保留原模型对照，
仅用训练集校准并披露改动。

ALTO 的在线 `pte_scale` 控制仍作为参考能力保留。首轮显式访问路径实验
隔离自动迁移；后续若比较完整 SoarAlto，必须实际运行其评分/放置和 ALTO
控制，并验证页位置。只使用 ALTO 内核不等于运行 SoarAlto 策略。
不再为体现关联而强制实现三组件协调系统。

### TierTrain 与创新核查

TierTrain（ISMM 2025，DOI 10.1145/3735950.3735956）已经覆盖 CPU DNN
训练的生命周期、主动卸载/预取、部分迁移与反馈，不能把这些重复作为创新。
其真实 CXL 容量收益与 Optane 容量受限加速要分开引用。

第一周复核论文对迁移收益、stay time、选择性卸载/预取的具体条件，并检索
后续及其他收益感知方案。“TierTrain 总是无条件预取”不能作为未经核实的前提。
候选贡献是训练场景下可测量的访问路径差异、关键性特征的预测价值，以及
同等资源约束下的简单有效策略；是否新颖须以文献和实验为准。

## 4. 环境与实验控制

每批实验保存拓扑与系统状态：

```bash
uname -a
lscpu
numactl --hardware
cxl list -M
free -h
cat /proc/sys/kernel/numa_balancing
cat /proc/sys/kernel/numa_balancing_pte_scale
cat /proc/sys/kernel/perf_event_paranoid
cat /sys/kernel/mm/numa/demotion_enabled
```

若缺少 cxl-cli 或无权枚举，记录错误，通过 sysfs 的 CXL 设备、region/memdev、
NUMA 映射交叉确认，不能仅凭 memory-only node 就断言是真实 CXL。
确认容量、在线状态和逐页驻留后再执行实验。

当前基准环境为 6.8.12-138-soaralto、PyTorch 2.6.0+cpu、CPU 节点 0、
DRAM 0 / CXL 2；首先 CPU 0–7，随后同插槽 16 物理核。记录实际 LLC 容量，
不要把双插槽总缓存当单插槽可用缓存。线程、oneDNN、Python、频率策略、
精度、种子和优化器固定；BF16/AMX 留作后续独立扩展。

正式显式迁移对照优先在维护允许时关闭 NUMA balancing，保存/恢复原值；
不能修改时，使用相同 MPOL_BIND 保护、相同背景设置并验证驻留，披露限制。
已有 pilot 使用后者，未改全局设置。仅受管映射 MADV_NOHUGEPAGE；
不在线下线主机内存，不更改默认启动内核。性能实验串行运行并记录背景负载。
perf/系统参数确需 sudo 时再使用受限包装，异常也恢复设置。

## 5. 工作负载：先算子，再真实网络

1. 保留 MLP 作为正确性回归。增加 Linear/GEMM、Conv2d、LayerNorm、GELU、
   Softmax 的少量 backward 案例，选择其实际保存的 tensor。
2. 首个网络用小型 Transformer Encoder：固定序列长度、显式 unfused attention，
   避免不同 shape 悄悄改变融合路径。用真实 forward/backward/optimizer 计算，
   输入先用固定种子的合成数据，明确不是收敛精度实验。
3. 第二个网络用小型 CNN，随后扩展 ResNet-18/34；逐项确认非原地激活和 hooks
   支持范围。BERT 及更大模型仅在上述闭环成立后引入。

首批算子覆盖约 0.5×、2×、8× 单插槽 LLC 的可行工作集；网络先用两档
batch/sequence shape，使受管工作集从缓存内扩大到明显超过 LLC。
以实际保存量和峰值测量验收，不预设某个模型天然产生内存压力。
超过 LLC、受管池 DRAM 预算受限、整个服务器内存不足是不同条件。
第一阶段不靠耗尽服务器内存制造收益。

## 6. 基线定义：受管池与整进程分开

| ID | 策略 | 用途 |
| --- | --- | --- |
| N0 | 原生训练，整进程匿名分配绑定 DRAM | DRAM-only 性能参考 |
| N1 | 原生训练，整进程匿名分配绑定 CXL | 整进程 Direct-CXL 参考 |
| O0/O1 | 仅 hooks / 相同受管副本全部在 DRAM | 分离观察和复制开销 |
| D | 所有合格受管 tensor 直接在 CXL 使用 | 核心 Direct 对照 |
| P-sync | 与 D 相同初态，在需求时同步迁回 | 迁移成本诊断，非强预取基线 |
| P-async | 所有合格受管 tensor 在可用窗口中提前迁回 | 核心 Always-Prefetch 对照 |
| S | 同一异步执行器中选择性预取 | 后续候选策略 |

N0/N1 用 CPU 同节点绑定及对应 memory policy，查询实际驻留；共享库/文件页
等例外单列。它们改变的对象比受管策略多，只作整进程参考，不拿 N1 与
只迁 saved tensors 的 P-async 做唯一因果比较。现有 `dram/cxl` pilot 是
受管池放置，不能改名为整进程 DRAM-only/CXL-only。

D/P-sync/P-async/S 使用同一 workload、相同受管集合、buffer 实现、复制过程、
非受管内存位置和初始 CXL 驻留。第一版沿用 pack 时直接复制到 CXL；如果
扩展为 DRAM→CXL 卸载，所有相关策略都采用同样路径，迁出开销计入总时间。

Always-Prefetch 指所有合格对象都尝试调度，在相同容量/带宽/队列约束下执行，
不是无限提前把所有数据塞入 DRAM。预算不足时采用固定、公开的需求期限顺序，
记录排队、迟到、回退；不能故意选择弱触发时机以放大候选策略优势。

## 7. 最小实验实施顺序

### E0：补齐正确性和测量入口

扩展已有 `observe_saved_tensors.py` / `numa_buffer.py`，参数化 workload、shape、
线程和模式；保留旧 pilot 可复跑。验证别名、非连续 view、原地修改、重复 unpack、
图释放和异步工作者异常。不能支持的对象显式排除并报告覆盖率。

独立 mmap 必须页对齐，只迁移其自身页面。逐页检查 move_pages 返回状态，
部分失败计数并使该次结果标记异常；不可默默删除失败样本。数值检查 loss、
各参数梯度和更新后参数、NaN/Inf，初始阈值 rtol=1e-4、atol=1e-6。

将“详细驻留/生命周期诊断”与“低开销性能运行”分开。性能运行保留策略必需
的同步、预算和决策成本，不能把这些成本移出计时；详细诊断作为独立同配置
验证，另测其开销。记录前向、反向、优化器和完整 step 边界。

### E1：建立真实异步预取

在 tensor 的实际空闲窗口提交后台迁移，在 unpack 前检查完成状态；迁移期间
持有 buffer 生命周期引用，同一 buffer 只有一个迁移者。需求提前到达则等待
已有任务结束，记录暴露的等待时间；不得边释放边迁移或重复提交。

第一版一个工作者和明确 CPU 亲和性；所有策略保留同样计算核资源。验证
后台 move_pages 确实与计算重叠，而不是只创建线程却仍串行执行。
根据前几轮观察得到下一轮触发点，探索/预热开销记账，不使用未来真实需求
构建可部署策略。算子诊断可以扫描受控 overlap window；主结论必须来自网络
自身计算窗口。不得插入额外 sleep 或只给预取模式增加计算来制造隐藏机会。

### E2：单对象干预，寻找决策空间

先在每个算子两档 shape、8 核下比较 D/P-sync/P-async 和 DRAM 参考；
再在 Transformer/CNN 中按层、大小和算子族预先选定对象组，逐一只改变
一个对象的访问路径，其他放置保持不变。同种 tensor 可以在不同预算或
窗口下最优动作不同，因此记录完整上下文，不能把动作当对象永久属性。

只有真实网络中出现可重复差异后才扩展 16 核、更多 shape 和全受管集合。
单对象收益不可直接相加，全策略执行时要重新测共享带宽、缓存和队列交互。

### E3：Hotness、AOL 与实测收益

PEBS 按地址范围和实际 buffer 存活的半开时间区间匹配，防止 allocator 地址
重用串样。跨迭代 ID 使用算子/模块路径、保存序号、shape、dtype 和代际，
不以裸地址作持久标识。Packed 释放不一定等于底层 storage 最后使用，
注册表必须跟踪真实 buffer 生命周期。

先取得 D 路径下可用特征，分别比较 hotness、AOL、原 SOAR score、大小/窗口
简单参考模型。不能把“高频+高 AOL”等标签手工赋给 tensor。顺序/随机模式
只在可证明的算子或受控微基准中标注；pointer chasing 是环境诊断，不冒充
典型 DNN tensor。报告每个特征的估计口径、样本量和未覆盖对象。

### E4：最小收益策略

只有 E2 支持决策空间、E3 证明可用特征后实现简单阈值/标定模型。
容量可行性先作为硬约束，窗口/队列决定预取能否及时完成；在可行对象中
按预测净收益或收益/占用量选择。不足以判断的对象采用固定公开回退。
分别消融 hotness-only、size/window-only、AOL-only、SOAR score，以及加/去
迁移成本；证明 AOL 的增量价值，不能只与总是预取比较。
ML 留待简单策略有收益后再考虑，若使用优先直接预测预取净收益。

## 8. 收益定义与量纲

主要标签是在相同控制条件下的配对端到端时间差：

    Delta_i = T_step(Direct for i) - T_step(Async-prefetch for i)

正值表示预取有利，负值表示直接访问有利。单 tensor 干预时其余策略一致；
全策略比较直接使用整个运行/step 的时间差，不相加各对象的 Delta。

同步诊断可近似写为：

    T_sync = T_migration_exposed + T_DRAM_compute + T_management

异步情况必须考虑重叠与干扰，一个仅供标定的近似模型为：

    NetBenefit_hat = SavedComputeTime_hat
                     - ExposedMigrationTime_hat
                     - InterferenceTime_hat - ManagementTime_hat
    ExposedMigrationTime_hat ≈ max(0, QueueDelay_hat + MigrationTime_hat - Window_hat)

该近似不替代实测；迁移与计算争带宽时各项并不独立。若实测 T_prefetch 已是
端到端时间，就不能再减一次完整迁移成本。迁移时长、等待时长和阶段时长
分别记录，后台全部迁移时长不能直接与计算时长相加。

原始 AOL/SOAR score 不以秒计，不能直接减迁移秒数或 DRAM 字节数。若建立
时间收益模型，用独立校准数据拟合并报告误差；DRAM 压力先用预算硬约束，
只有明确转换/归一化后才作为额外代价项。

## 9. 容量与资源公平性

先做容量充足实验确认机制，再限制受管池 DRAM 峰值：全 DRAM 受管参考峰值
的 25%、50%、75%。以固定对象集合计算参考，不在不同策略下重新定义预算。
不将该预算称为整进程 DRAM 限额；memory.max 也不是 DRAM 层容量限制。

预取提交前为全部目标页预留快层额度，记录预留和实际驻留，释放/迁出确认后
归还。临时目标复制、迁移 staging 和原副本的同时占用必须计入测量或采用
所有策略一致的明确额外额度。报告受管峰值/平均驻留、byte-seconds、
进程 RSS/NUMA 占用和非受管内存。不能仅靠减少 prefetch 次数宣称节省容量。

## 10. 指标与测量可得性

| 指标 | 首版来源与限制 |
| --- | --- |
| Step time、throughput、总耗时 | 单调时钟；固定样本或 token 数，含策略在线成本 |
| Epoch time | 仅在定义了真实 epoch/数据集时报告，不把合成 steps 改称 epoch |
| Tensor 保存/需求间隔、大小、生命周期 | hooks + registry；unpack 不等于最终使用结束 |
| Hotness | PEBS 事件限定的访问采样率/密度；不是精确全部读写次数，也不是 unpack 次数 |
| AOL / SOAR score | 复用原事件定义与评分；记录公式、事件配置、区间和覆盖 |
| LLC miss、MPKI、memory stall、MLP 代理 | perf/GNR 支持事件；进程/区间计数不能冒充 tensor 精确计数 |
| Read/write、顺序性、reuse distance | 仅可观测范围；load-only PEBS 不提供写流量，稀疏采样不提供精确 reuse distance |
| 迁移请求、成功/失败页数、方向字节数 | 执行器日志 + move_pages 逐页状态；请求字节不等于成功迁移字节 |
| CXL/DRAM 流量、带宽 | 可用的 CXL/uncore PMU；共享设备计数需记录背景，不能直接归因某 tensor |
| 预取提前量、迟到率、暴露等待 | submit/done/unpack 时间与完成状态 |
| DRAM 占用、eviction、pressure | 受管账本和页查询、进程 NUMA/RSS；实际使用过才报告 eviction |
| 决策质量 | 测试集 Direct/Prefetch precision/recall、无差异比例、误判时间损失/regret |

硬件事件不可用时标 N/A 并继续计时/迁移实验，不用迁移字节冒充总 CXL 流量。
PMU 事件先检查 GNR 支持、time-enabled/running、复用、丢样、权限和时钟对齐。
对稀疏样本不强行评分；同时报告按对象数和字节数的覆盖率。
详细 profiling 与常规计时分轮进行，部署时必需的采样成本必须计入策略结果。

## 11. 重复、判断标准与图表

每配置先小规模 pilot，再至少 5 个独立进程重复，各 5 个预热、20 个计时 step
作为起点；未稳定就延长并记录。策略成对轮换/随机顺序，共用种子与初始化。
同一进程里的 steps 不是独立样本，按独立运行计算配对差值和 95% 置信区间。

先测同配置重复噪声，固定实用差异门槛（初始 3% 与噪声门槛取较大者，
只在探索阶段校准，测试前冻结）。区间跨 0 或差异不足门槛的对象列为“不确定/
近似持平”，不能强行分成赢家。多对象探索发现需在独立重复中确认。

特征相关性用 Spearman 及区间；同时控制 size、算子、窗口等混杂因素，
检查增量预测价值。训练/校准和测试按模型或 shape 分组，不能随机拆分同一
对象相邻迭代泄漏信息。事后最佳选择只作探索参考，不称为可部署 oracle，
更不能在存在交互时称严格性能上界。

计划图表：对象/场景的 Delta 热图及区间；窗口—大小—收益图；hotness/AOL/
SOAR score 与收益散点；各策略 step time 与 DRAM 占用折中；迁移与总流量；
决策混淆矩阵和误判时间损失。正负结果、失败和退化全部保留。

## 12. 最终对照与可选 ALTO 扩展

核心对照为 N0/N1、受管 D、强 P-async、收益策略 S；P-sync 只作诊断。
随后增加 TierTrain、SOAR tensor 适配、完整 SoarAlto 兼容版本及特征消融。
不同管理范围或容量控制的系统单列，不做虚假的同预算排名。

TierTrain 优先官方可运行实现；不可得时明确标为重实现，并对齐 ST、队列、
部分迁移和反馈。仅提前一层的实现只能称简化语义预取，不能冠以 TierTrain。

若后续加入 ALTO，先验证保护/解除后可扫描性，防止内核提升抵消 Direct 决策。
扫描开放前为潜在提升预留容量；原始建议和实际 pte_scale 都记录。固定扫描与
动态 ALTO 使用同样预算和主动执行器，单独验证 ALTO 增量；无增量也不否定
已经独立验证的选择性预取问题，但不能宣称三组件融合有效。

## 13. 交付文件与代码入口

新增研究代码保持在 `research/cpu_training/`，保留原有核心和旧实验入口。
现有：`observe_saved_tensors.py`、`numa_buffer.py`；拟逐步拆分 registry、
workloads、prefetch executor、policy、runner 和 analysis，不预先搭建大框架。

复用：`src/soar/run/proc_obj_e.py`、`profile_intervals.py`、
`run/placement_policy.py`、`configs/gnr-events.json`；可选 ALTO 入口：
`run/bc-urand/set_scan_scale.py::decision`。

每次使用新结果目录，保存源码 hash/快照、manifest、依赖、拓扑、线程、预算、
种子、workload、完整 stdout/stderr、correctness、step-times、tensor-events、
migration、residency、budget、sample-coverage、features、scores、decisions
及原始 perf 日志。记录首次 profiling/校准成本和长期摊销成本。

## 14. 分阶段排期与继续/停止条件

| 阶段 | 建议时间 | 验收和决策 |
| --- | --- | --- |
| Phase 1 基础 | 已有基础，首日复核 | SOAR/ALTO 与真实 CXL 环境、旧 MLP 正确性 |
| Phase 2 访问路径 | 第 1–3 天 | E0/E1：参数化、首个网络、真实异步预取、驻留与正确性 |
| Phase 3 机制测量 | 第 4–5 天 | E2：D/P-sync/P-async 成对结果；trace/PMU 可用性 |
| Phase 4 问题判断 | 第 6–10 天 | 网络单对象差异、独立确认、初步 E3 与文献核查 |
| Phase 5 最小策略 | 第 3–4 周，仅前关通过 | E4：相同资源下的启发式、开销和消融 |
| Phase 6 系统比较 | 第 5–6 周 | 扩展网络/shape、TierTrain/SoarAlto、失败案例 |
| Phase 7 后续取舍 | 第 7–8 周 | 整理证据，判断是否值得 ML/扩展/论文；不承诺发表 |

第一批直接任务：参数化既有原型 → 小 Transformer 正确性 → 相同受管集合的
D/P-sync/P-async → 两档 shape、独立重复 → Q1 判断。
PMU 采样可用性尽早探测，但 Q1 不依赖先做完整 AOL 模型。

继续条件是：真实网络存在超出噪声的两类最优动作，或资源/窗口变化产生稳定
动作边界；区别能影响整步时间或时间—容量折中，且不是只有同步迁移劣化。
AOL 是否有效作为独立问题判断，不能以结果好看为由省略对照。

失败分支：

- 所有可行场景 Always-Prefetch 最好：记录范围，停止宣称选择空间；只在有
  现实依据时扩展预算/窗口，不能无限调参寻找负例。
- 所有对象 Direct 最好：先排除预取实现差、窗口错误；若仍成立，当前选择策略
  无明显必要，可研究迁移成本边界，但不能虚构混合策略贡献。
- 只有算子差异、没有网络收益：检查执行开销/缓存效应，先不扩展完整系统。
- AOL 不优于 hotness 或 size/window：保留负结论，不能再声称 AOL 是有效核心；
  Q1 仍可能成立，可评估更简单模型并明确研究定位变化。
- 选择策略减少迁移却不降低训练时间：仅报告流量/容量折中，不能声称加速。
- 同预算不公平、NUMA 位置不符、正确性不通过：暂停性能结论，先修执行与测量。
- 文献已覆盖主要选择机制：重新界定差异与证据，不能将应用迁移包装为创新。

当前目标是完成 Q1–Q3 的可信验证。只有通过这些检查，才投入更复杂预测、
页面级细化、在线 ALTO 联动或论文系统化工作。

## 2026-09-23 测量审计更新

修复生命周期仅保留最后一步及首次 unpack 被误当释放边界的问题；此前
共享窗口 AOL 结果不能用于否定 AOL。完成六对象 18 次正确性及 90 次计时，
尚未发现稳定预取赢家；PEBS 首次获得 266 个正式步骤受管地址匹配样本，
仍不足以做对象相关性结论。权限当前为用户开放的 -1。详见
[测量审计与新结果](../research/cpu_training/MEASUREMENT_AUDIT_0923.md)。

## 单对象采样覆盖更新（2026-09-23）

已完成六目标各三次、每次 500 步的 Direct 单对象 PEBS 采集；18 次全部
成功，ID2/16 的零样本问题已转为可观察的低覆盖。对象背景 AOL 区间重叠，
暂不据此否定原指标。训练步数与旧计时对照不同，不直接跨实验拟合收益。
详见 [单对象 PEBS 覆盖报告](../research/cpu_training/SINGLE_PEBS_REPORT.md)。

## 500 步阶段对齐结果（2026-09-23）

完成六对象 Direct/ranked 各三次计时（36 进程），与独立 PEBS 特征的
完整 505 步 loss 和源码一致。平均预取净收益均为负，尚无稳定预取赢家；
六对象描述性相关性不足以否定 AOL。详见 [对齐实验报告](../research/cpu_training/MATCHED_500_REPORT.md)。

## 预取原因验证的初步结论（2026-09-23）

完成两档三方对照共 63 次计时：未发现稳定初始 DRAM 放置优势；大配置
32 MiB 目标的 ranked 比 Direct 慢约 32.4 ms/step，约 29.0 ms 出现在
forward，需求处等待仅约 0.046 ms。预取流程额外成本是当前主要问题，
具体的软件/迁移/缓存机制尚未分离，不据此否定 AOL。见
[三方对照与初步结论](../research/cpu_training/PLACEMENT_CAUSE_REPORT.md)。
