# 新会话接力说明（2026-09-29）

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
没有全进程 DRAM 容量限制，不是整进程 CXL-only。保存张量 hook 对所有受管候选
执行新 mmap/mbind/copy，不仅 ID10；MADV_NOHUGEPAGE。不能称为完整 TierTrain
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
   优先机制：迁移与前台分配相互干扰；尚未干预验证其整步因果贡献。
   百分比是整段线程生命周期的内核周期样本权重，含预热，不是函数墙钟占比。

## 从这里继续

先读 KERNEL_MIGRATION_PROFILE_REPORT.md、NATIVE_MIGRATION_REPORT.md、
MIGRATION_CAUSE_ANALYSIS.md 和 ../../docs/CPU_TRAIN_CXL_PLAN.md。
不要重复已完成的矩阵或要求用户重跑本次 sudo 采样。

下一步优先增加缓冲区预分配/复用对照，降低正式前向中的新映射/首次缺页。
Direct 与 Prefetch 同时应用；保持 ID10 每步初始在 CXL、跨节点迁移量 32 MiB。
重置/迁回必须纳入公平端到端计时，单列初始化，检查生命周期、别名、梯度/参数、
驻留及释放。如果不能公平实现，先做独立分配压力对照。正确性通过后随机顺序、
至少三次独立进程，比较整步、前向、native CPU/墙钟、缺页和迁移量，必要时单独
采样验证 LRU 自旋是否下降。此干预尚未实现或运行。不要直接删除 lru_cache_disable。

## 文件入口与证据

- 修改入口：access_paths.py、numa_buffer.py、run_access_paths.py；C 包装器 migration_meter.c。
- 内核热点分析：analyze_kernel_profile.py；采样脚本 ../../run/profile_native_migration_kernel.sh。
- 原始结果：results/migration-{ablation,vectorized,timing,native-diagnostic,native-timing,native-isolation}-0929/
  （从仓库根目录查实际目录）；内核采样 results/migration-native-kernel-profile-0929/。
- 内核有效分析在上述采样目录的 verified-analysis/；原 worker-report.txt 保留但归属不完整。
- 可随 Git 传递的轻量证据在 evidence/migration-*-0929/。原始 perf.data、
  kallsyms.txt、二进制及完整大型结果留本机，不公开提交。
- 读取 git status / git log 确认最新提交，不覆盖未提交修改。
