# 新会话接力说明（2026-10-09）


## 最新断点：两项收敛实验及分段确认已完成

先读 [CLOSURE_REPORT_1009.md](CLOSURE_REPORT_1009.md)，不要重复下面历史段落的待办。
最新结果已归档在 evidence/closure-1009/；尚未提交/推送，git 状态中的新增代码和证据均需保留。

- 早期 10-08 alloc-pressure v1–v3 和 target-placement 旧结果不公平，已明确撤回，见归档 invalid-prior-runs.json；旧 verified-analysis 名称不代表仍可用。
- 压力 2×2 的严格版已完成。10-08 v4 不能完全排除尾部测试启动重叠；10-09 v5 串行重跑 12 个进程并通过审计，是主要证据。
- v5 fresh/reuse 的 forward native 为 18.992/14.655 ms，差 4.337 ms [4.132,4.542]；前台差中差 3.192 ms。整轮含回迁差中差 0.739 ms，完整 run 摊销差中差 0.035 ms 的区间跨零。实际重叠时长不同，不能单归因 LRU。
- 公平目标放置：DRAM/CXL 都让 ID10 每步 fresh，只有非目标 node0 的 102.15625 MiB 副本复用。10-08 三方 9 进程已完成；10-09 新分段诊断及 9 进程也完成。
- 10-09 Direct/Prefetch 为 88.047/107.036 ms，Prefetch 慢 18.989 ms [12.322,25.655]。DRAM 为 92.535 ms；DRAM−CXL 差 4.488 ms 的整步区间跨零，不声称稳定 CXL 硬件优势。
- ID10 创建 DRAM/CXL 为 2.649/2.914 ms，缺页均 8192；DRAM 目标创建更快，不能解释其整步较慢。其余 forward 包含其他副本与分配，不是纯计算。
- 35 项回归、五项审计故障注入通过；均在计时结束后运行。无运行中矩阵，无 sudo/权限阻塞。
- 所有原始目录保留；性能使用各自 verified-analysis。原始根目录：results/closure-pressure-timing-1009-v5、closure-placement-pack-{diagnostic,timing}-1009-v1；诊断参考详见报告。

下一步不再调 ID10 迁移参数：若继续验证收益边界，先有限重放实际 backward 算子，公平测 DRAM/CXL 访问及完整创建成本，出现访问优势后才扩展另一对象/shape 并回到训练验证。仍未做 AOL 收益关联或复杂 ML，不随意改内核。

## 目标与约束

基于 SoarAlto 的 AOL / Performance Criticality，研究真实 CXL 上 CPU DNN 训练的
Direct Access / Selective Prefetch 收益。先建立可信基线和正负收益证据，
再引入 AOL 或简单收益策略；不追求复现论文数字，不提前加入复杂 ML。
中文沟通和文档，实际运行实验；常规问题自行解决。用户可 sudo，需要密码时提供
完整命令。保留原始数据，结果目录不可覆盖，不随意改内核或全局参数。

## 仓库与环境

- 工作目录：/home/hjy/projects/TieredMemoryManagementBeyondHotness/SoarAlto。
- 用户仓库：git@github.com:aoersh/-AOL-TieredMemory.git，remote=research，分支 main。
- origin 是上游 MoatLab/SoarAlto，日常研究工作不要推到 origin。
- 本轮内核为 6.8.12-138-soaralto；DRAM node0/1，真实 CXL node2/3。
- 训练 CPU0–7，迁移线程 CPU8；本轮 perf_event_paranoid=-1、numa_balancing=1、
  kptr_restrict=1，下一次运行前核对实际值，不假定永久不变。
- 使用 kernel/build-perf/perf；/usr/bin/perf 在定制内核上不可直接用。
- 原生计时库 .deps/training-native/libmigration_meter.so，构建入口
  research/cpu_training/build_native_meter.py。已有结果保存了执行版本，勿中途重建。

## 当前实验的准确范围

CPU eager FP32 Transformer，batch8、sequence512、width128、heads4、layers2、SGD。
仅 saved tensor ID10（32 MiB，8192 个 4KiB 页）初始放 node2，其余受管候选放 node0；
没有全进程 DRAM 容量限制，不是整进程 CXL-only。fresh 条件的保存张量 hook 对
所有受管候选执行新 mmap/mbind/copy；09-30 新增 reuse_dram，仅复用非目标
node0 的 102.15625 MiB 副本映射，每步仍复制；ID10 仍每步新建，不需回迁。
所有受管映射 MADV_NOHUGEPAGE。不能称为完整 TierTrain
复现或完整 SoarAlto 策略，不能据此否定 AOL。

## 已完成及结论

1. 迁移参数构造向量化、固定启动延迟、同节点控制、C 内计时、独立迁移对照均已完成。
2. 无采样的三轮 native 矩阵：Direct 整步约 102.374 ms，向量化跨节点约 137.150 ms；
   跨节点 C 内调用约 50.016 ms、线程 CPU 49.825 ms；同节点约 4.913 ms。
   外层 Python 与 C 内时间差仅约 0.02–0.04 ms，900 次最大约 0.148 ms。
   不支持 GIL 返回等待或长时间睡眠是主要成本；仍未发现稳定预取赢家。
3. 无并发 DNN 的独立 32 MiB 跨节点约 16.485 ms，但不是严格配对训练反事实，
   不可将 50−16.5 ms 全归于带宽竞争。
4. 用户完成三组 sudo cycles:k profile（每组 5 预热+100 正式），记录丢样为零。
   默认 perf report 的同名线程聚合导致 --tid 遗漏，已修复为
   --sort comm,pid,dso,symbol --percentage relative，并从原始数据核对样本/period。
   worker 样本：同节点 167、向量化跨节点 1066、原准备跨节点 1050。
5. 两种跨节点 worker 的 LRU 路径自旋权重为 40.70% / 41.63%，copy_page 为
   18.78% / 16.76%。其他线程也在匿名缺页后的 LRU 入队路径大量自旋。
   优先机制：迁移与前台分配相互干扰；09-29 当时尚未干预验证其整步贡献。
   百分比是整段线程生命周期的内核周期样本权重，含预热，不是函数墙钟占比。
6. 09-30 完成非目标 DRAM 缓冲复用干预：四条件各三次独立进程，每进程
   5 预热+100 正式，另四组三步诊断。32 项回归通过，loss/梯度/参数误差为 0，
   驻留与 storage 别名生命周期通过；每步新受管映射由 134.15625 降到 32 MiB。
   fresh Direct/Prefetch 为 104.743/133.156 ms，reuse 为 88.560/107.460 ms。
   额外预取成本差中差降低 9.513 ms [4.581,14.445]，C 内迁移由 48.867 降到
   36.301 ms；但复用 Prefetch 仍慢 18.900 ms [12.946,24.854]，没有预取赢家。
   初始化、池关闭与全部预热计入完整运行后结论不变。该轮为无采样实验，不能将
   上述下降全归因于 LRU 自旋，复用还改变缓存、地址和 DRAM 保留量。
7. 随后完成 fresh/reuse × Direct/Prefetch 各三进程新内核采样。当前有效分析为
   results/buffer-reuse-kernel-profile-0930-v1/verified-analysis-v2/。共 219017 样本，
   其中 202249 位于正式 step，零丢样。每步 worker LRU 自旋权重从 66.051 降到
   33.094 百万采样周期，前台匿名缺页/LRU 从 551.995 降到 281.901；98% 以上
   前台该路径权重发生在 native 调用期间。copy_page 30.664→29.408，差值区间跨零。
   PTE 页表锁自旋也显著下降，不能把所有改善归于 LRU；没有锁地址/墙钟因果分解。
   普通用户能记录 IP；自动 perf 符号失败，改用已核验的同内核 build-ID/同启动
   符号快照解析。旧数据 196050 核心栈帧与 perf 符号全部匹配，九项解析测试通过。
   不解析模块/范围外地址，全部保留分母；重启后不能继续使用这份地址快照。

## 09-30 历史交接（下列独立 2×2 已于 10-09 完成）

先读 BUFFER_REUSE_KERNEL_REPORT.md、BUFFER_REUSE_REPORT.md、
KERNEL_MIGRATION_PROFILE_REPORT.md、NATIVE_MIGRATION_REPORT.md、
MIGRATION_CAUSE_ANALYSIS.md 和 ../../docs/CPU_TRAIN_CXL_PLAN.md。
不要重复已完成的矩阵或要求用户重跑本次 sudo 采样。

非目标复用和新干预下 fresh/reuse 内核采样均已完成，不要再当作待实现任务。
下一步优先固定迁移目标的独立 2×2 对照：前台新映射/首次写入 vs 已触页复用，
分别配迁移 vs 不迁移，进一步区分 LRU、PTE 与分配器/缓存。固定写入量、CPU、
32 MiB 目标和初始 node2 驻留，记录实际重叠、native、前台与完整成本，先正确性
再随机三进程。必要时后续少量改变前台线程数；不直接改内核。
09-29 的采样已修复，无需重跑修复。
当前只复用 node0 非目标副本；若扩展 ID10 复用，必须显式纳入逐步回迁/重置，
不能从计时中删掉这部分。不能把这次差中差直接叫作 LRU 自旋贡献，不直接删除
lru_cache_disable。保持每步初态、迁移量、正确性，再随机顺序至少三次进程。
尚无预取赢家，暂不推进复杂 ML；AOL 的关联验证仍待可信收益边界。

## 文件入口与证据

- 修改入口：access_paths.py、numa_buffer.py、run_access_paths.py；C 包装器 migration_meter.c。
- 复用矩阵/分析：run_buffer_reuse.py、analyze_buffer_reuse.py、test_buffer_reuse.py。
- 新机制采样/分析：profile_buffer_reuse.py、analyze_buffer_reuse_kernel.py、
  test_buffer_reuse_kernel.py；轻量 evidence/buffer-reuse-kernel-0930/。
- 新采样原始数据：results/buffer-reuse-kernel-profile-0930-v1/，用 verified-analysis-v2/；
  探针/符号核验 results/buffer-reuse-kernel-probe-0930-v1/。旧/新版分析都保留。
- 09-30 原始数据：results/buffer-reuse-{diagnostic,timing,validation}-0930-v1/；
  有效性能分析在 timing 目录的 verified-analysis/；轻量 evidence/buffer-reuse-0930/。
  早期 results/reuse-pool-diagnostic-0930/ 只是冒烟检查，不与正式计时混用。
- 内核热点分析：analyze_kernel_profile.py；采样脚本 ../../run/profile_native_migration_kernel.sh。
- 原始结果：results/migration-{ablation,vectorized,timing,native-diagnostic,native-timing,native-isolation}-0929/
  （从仓库根目录查实际目录）；内核采样 results/migration-native-kernel-profile-0929/。
- 内核有效分析在上述采样目录的 verified-analysis/；原 worker-report.txt 保留但归属不完整。
- 可随 Git 传递的轻量证据在 evidence/migration-*-0929/。原始 perf.data、
  kallsyms.txt、二进制及完整大型结果留本机，不公开提交。
- 读取 git status / git log 确认最新提交，不覆盖未提交修改。
