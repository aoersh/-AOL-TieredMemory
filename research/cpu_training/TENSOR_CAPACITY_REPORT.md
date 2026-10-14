# 原生框架校准与无保存副本的张量容量筛查（2026-10-13）

## 问题与预设范围

承接10-12停止attention保存副本调参的决定，本轮先量化旧hook副本框架相对原生
训练的成本，再在没有saved副本的原生计算图中约束全部C10 CPU张量存储。
不修改内核、sysctl、SOAR原算法或旧实验入口；不增加形状、人工复用或sleep。

固定FP32 eager Transformer、batch8/sequence512/width128/heads4/layers2、SGD，
训练CPU0–7，DRAM node0/真实CXL node2。仍为确定性合成输入，不是生产训练收敛。

框架校准三条件（native/dram/direct）各五独立进程，5预热+100正式，seed20261013
随机串行；先每条件三步诊断。native不安装保存hook，但保留旧runner公共账目和
空闲CPU8工作者；dram/direct对全部合格保存张量fresh复制134.15625 MiB/步。
它量化的是整个副本框架的额外成本，不能把差值全部归为memcpy或hook调用。

## 容量实现及公平边界

通过PyTorch已有 `c10::SetCPUAllocator` 接口注册进程内CPU分配器，首次分配时
直接mmap并mbind到0或2；没有保存副本复制，没有迁移。4096字节页对齐预约，
MADV_NOHUGEPAGE，预约持有到storage最后引用释放并munmap完成，才退还额度。
同一锁保护准入和退款；DataPtr及raw_deleter保留PyTorch的storage别名生命周期。
容量不足时新张量放CXL，不驱逐既有张量。

范围包括安装之后经过C10 CPU allocator的参数、输入、梯度、保存源张量和临时
张量存储，区别于旧方案只限制额外saved副本。解释器、Python元数据、库内部
直接分配、共享文件页及安装前张量不受限，**不能称为整进程DRAM物理容量上限**。
诊断核查参数/输入、全部保存张量及参数梯度的storage归属，并核查所有活跃
分配记录的逐页驻留、页对齐额度和最后释放。进程匿名驻留单列，体现账本外内存。
保存hook仅用于诊断覆盖、返回detach视图，无复制；正式计时没有保存hook。

两个64/128 MiB预算预先固定，不根据速度追选预算；同一模型的全DRAM张量预约
峰值必须超过128 MiB，否则容量筛查不成立。七条件各三独立计时进程，5预热+
100正式，另七组三步诊断，seed20261013随机串行：

| 条件 | 分配策略 | DRAM张量预约上限 |
|---|---|---|
| native | 默认PyTorch allocator，无hook | 无约束性能参考 |
| dram | 新allocator全部放node0 | 无约束性能参考 |
| cxl | 同一新allocator全部放node2 | 0 |
| first64/128 | 按分配顺序，能放进剩余额度则DRAM，否则CXL | 64/128 MiB |
| small64/128 | 仅逻辑大小≤1 MiB优先DRAM，再检查剩余额度 | 64/128 MiB |

first和small是首次分配准入规则，不是页面迁移、热度、AOL或同一对象反复复用。
实际使用额度可以小于上限，不人为占满。cxl是预算不使用的参考，真正相同预算
的选择差异是small与first。无约束dram/native不能充当同预算竞争者。
新allocator每次mmap，可能有其管理开销；dram/cxl/first/small共用该实现，
另用native量化这项开销。后端内部直接malloc的工作区仍在范围外。

## 计时、诊断和停止条件

校准使用未修改的 `run_access_paths.run` 整步与完整run；另计入模型/输入构造的
workload_run。张量容量runner的整步包括zero_grad、forward、backward、optimizer、
loss取值和图释放；每步之后采样计数器，完整run包括安装、模型/输入构造、全部
预热、计数器采样和清理。两者均不包含Python导入/进程启动和最终JSON序列化，
因此不是进程启动到退出的全部墙钟。模型/输入构造不可伪称计入校准旧run。

诊断先在默认allocator下生成三步原生状态hash并释放，再安装新allocator。
实际三步的loss、全部参数梯度及更新后参数hash必须逐元素一致。
诊断中的内存查询、状态检查、参考训练不用于性能结论。RSS是进程历史高水位，
节点数是诊断时点，不是连续峰值，也不把两个独立峰值相加当同时占用。
move_pages对未触页可返回ENOENT；零页查询EFAULT仅在诊断中先用mincore检查
映射存在，再原值写回并重新查询，记录触页数，不放宽节点检查。

框架校准五进程配对t(df=4)，容量筛查三进程配对t(df=2)，均未校正多重比较；
步骤不是独立重复。固定3%实用门槛，区间跨零或未排除3%以内差异不称稳定实用收益。
本轮只筛查，不因点估计继续扩大shape。若同预算选择无实用证据，停止迁移/AOL
扩展；若出现候选差异，先独立确认，再考虑迁移，不能用首次放置收益直接认定迁移获益。

## 运行入口与原始证据

```bash
python3 research/cpu_training/run_framework_baseline.py results/framework-diag-new --diagnostic
python3 research/cpu_training/analyze_framework_baseline.py results/framework-diag-new
python3 research/cpu_training/run_framework_baseline.py results/framework-time-new --diagnostic-reference results/framework-diag-new
python3 research/cpu_training/analyze_framework_baseline.py results/framework-time-new --diagnostic-reference results/framework-diag-new
python3 research/cpu_training/build_tensor_capacity.py results/tensor-build-new
python3 research/cpu_training/test_tensor_capacity.py results/tensor-build-new results/tensor-tests-new
python3 research/cpu_training/run_tensor_capacity.py results/tensor-diag-new --allocator results/tensor-build-new --diagnostic
python3 research/cpu_training/analyze_tensor_capacity.py results/tensor-diag-new
python3 research/cpu_training/run_tensor_capacity.py results/tensor-time-new --allocator results/tensor-build-new --diagnostic-reference results/tensor-diag-new
python3 research/cpu_training/analyze_tensor_capacity.py results/tensor-time-new --diagnostic-reference results/tensor-diag-new
```

输出目录必须不存在；完整步骤、日志、库和内存映射记录保留本机results。
旧框架首轮diagnostic-1013-v1把共享文件页所在节点误作匿名分配边界，审计拒绝；
原始目录保留，修正后diagnostic-1013-v2是正式诊断参考。
张量分配器validation-1013-v1因未处理零页查询的EFAULT而拒绝resize测试；原始
目录保留，修正后validation-1013-v2五项检查通过。smoke是诊断，不作速度证据。

## 框架校准结果

有效原始结果是 `results/framework-baseline-diagnostic-1013-v2` 和
`results/framework-baseline-timing-1013-v1`，各自 `verified-analysis`。
三个条件各五进程均完成，数值状态hash一致，全部受管初始/使用节点及释放检查通过。

| 框架校准 | 整步 ms | 完整run/105步 ms | 进程峰值RSS MiB |
|---|---:|---:|---:|
| native，无保存副本 | 72.033 | 77.596 | 570.059 |
| 受管全部合格副本DRAM | 111.863 | 116.915 | 616.199 |
| 受管全部合格副本Direct CXL | 88.291 | 93.340 | 617.702 |

受管DRAM−native为+39.830 ms [28.007,51.653]；受管Direct−native为
+16.259 ms [6.862,25.655]，完整run差的区间也均大于0。受管Direct−DRAM为
−23.572 ms [−28.184,−18.959]。因此同一副本框架在两种放置下都有实质额外成本，
不能把其中Direct较快升级为CXL硬件优势，也不能把所有额外成本归为纯复制。
正式步骤缺页约native74718、受管DRAM134170、受管Direct132899次/步。

## 张量容量筛查结果

原始结果是 `results/tensor-capacity-{diagnostic,timing}-1013-v1`，分别使用
`verified-analysis`。七诊断进程和21正式独立进程全部完成，训练状态hash一致，
保存覆盖每步56次，参数/输入、保存storage和梯度的分配器归属检查通过。
诊断逐页节点与预约量吻合；全部正式run末端allocations=frees、live=0、failures=0。
没有页面迁移，不存在移出计时的迁回成本。

| 无保存副本路径 | 整步 ms | 完整run/105步 ms | 张量DRAM预约峰值 MiB |
|---|---:|---:|---:|
| 默认原生native | 67.289 | 73.578 | 未由新分配器计数 |
| 新allocator全部DRAM | 119.379 | 125.273 | 178.402 |
| 新allocator全部CXL | 102.937 | 108.853 | 0 |
| first64 | 98.687 | 104.704 | 63.895 |
| first128 | 105.397 | 111.262 | 127.855 |
| small64 | 104.527 | 110.458 | 3.324 |
| small128 | 104.977 | 110.813 | 3.324 |

同一新allocator各条件的张量总预约峰值都是178.402 MiB，预算低于该峰值。
small规则没有把多数大张量放DRAM，实际峰值仅3.324 MiB；64→128没有改变准入，
其两档不能被解释成真实DRAM容量效应。未人为填满空闲额度。

| 差值（左−右） | 整步 ms [95% CI] | 含义 |
|---|---|---|
| first64−cxl | −4.251 [−11.450,2.949] | 点估计快约4.13%，但区间跨零 |
| first128−cxl | +2.460 [−0.414,5.334] | 没有稳定加速 |
| small64−first64 | +5.840 [−5.386,17.067] | 同64 MiB预算没有稳定规则优先级 |
| small128−first128 | −0.421 [−7.901,7.060] | 同128 MiB预算没有稳定规则优先级 |
| first128−first64 | +6.710 [1.466,11.955] | 该规则放更多DRAM没有改善；不是一般硬件定律 |
| 新allocator DRAM−native | +52.089 [40.408,63.771] | 新分配路径本身有显著成本 |
| 新allocator CXL−DRAM | −16.441 [−22.447,−10.435] | 放置改变完整分配/计算环境，不能称纯访问速度 |

完整run口径保持相同判断；first64−cxl为−4.148 ms [−10.997,2.700]。
这些区间均为本批三进程配对，不与上面的五进程框架校准或10-12绝对时间拼接。

## 阶段决定与未解决问题

本轮成功扩大了容量账本的覆盖：从额外保存副本扩到全部经C10分配的张量存储，
并实现无保存副本的有限快内存准入。**尚无同预算选择或相对全CXL的稳定实用赢家，
不进入动态迁移或AOL阶段，也不继续扫描当前shape/预算/大小阈值。** 三进程筛查
区间仍宽，不能声称已排除所有可能收益；按预设有限筛查决定停在此处。

更明确的限制是新分配器本身代价大。正式步骤缺页约149481次/步，而同批默认
native约68404次/步；新实现逐分配mmap/mbind并在storage销毁时munmap，保留了
明显的分配/缺页压力，不能当成熟的低开销层级内存运行时。缺页与耗时共变是
证据，不是把52.089 ms全部归因于缺页、LRU或某个锁的因果分解。本轮没有perf采样。

同样不能宣称全进程64 MiB DRAM可训练：例如first64诊断第二步backward前，
进程无file字段匿名映射node0约380.637 MiB，张量预约node0仅63.805 MiB，
账本外匿名内存仍显著。该时点包含诊断参考遗留的默认分配器缓存，不是正式运行
峰值或精确后台工作区分解。共享文件页和file-backed私有COW也不能由此完全拆开。

若后续继续，优先解决可核验的容量边界与分配器自身开销，再谈策略：评估固定节点
有界池复用是否能在保留全部初始化/清理成本后接近默认allocator，并单列库工作区
与解释器残余。普通cgroup `memory.max`限制总内存，不是node0专用上限；仓库
现有SOAR拦截器也没有统一覆盖calloc/posix_memalign/mmap，不能直接套用并宣称
全进程DRAM受限。本轮未实施池化、全进程上限、热度、AOL或迁移优化。

五项新allocator行为检查在容量正式计时之前通过；全部计时完成后，十项在内存
破坏记录的审计检查均正确拒绝，包括复制量、时钟、匿名节点、数值、超额、泄漏、
storage/page节点和进程重叠。测试不修改原始输入，不与正式计时重叠。
