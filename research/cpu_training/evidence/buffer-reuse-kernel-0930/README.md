# 缓冲复用后内核采样证据（2026-09-30）

主报告：[BUFFER_REUSE_KERNEL_REPORT.md](../../BUFFER_REUSE_KERNEL_REPORT.md)。

四条件各三进程，每进程 5 预热+100 正式。219017 原始样本，202249 位于正式
step 半开区间。零丢样，TID 样本数和 period 权重独立核对通过。新采样不替代
无采样性能结果，训练源码/二进制哈希与 buffer-reuse-0930 证据一致。

- summary.json：完整组别权重、进程均值与区间，符号仅保留每组前十项、调用链前五项。
- summary.full.json.gz：未经裁剪的完整分析 JSON 压缩备份；原始版本为 verified-analysis-v2。
- runs/：每次 manifest、完整运行记账以及首预热/首中末正式步锚点。
- source/：本次执行、解析和测试源码；训练源码见 ../buffer-reuse-0930/source。
- validation/：196050 核心栈帧 oracle 核验、探针守恒、九项测试日志。
- raw-checksums.json：服务器原始 perf、步骤、日志等文件哈希，不包含其内容。
- symbol-provenance.json：同内核 build-ID、启动时刻和符号快照哈希。

Prefetch worker LRU 自旋 66.051→33.094 百万 period 权重/步；其余线程匿名
缺页/LRU 自旋 551.995→281.901；copy_page 30.664→29.408 的差值区间跨零。
PTE 自旋也大幅下降，不能把全部整步改善归为 LRU 的独立墙钟贡献。

本机数据：results/buffer-reuse-kernel-profile-0930-v1/；有效分析 verified-analysis-v2/。
初版分析保留。只读旧 perf.data 核验符号，没有重复旧 sudo 训练采样。
原始 perf.data、kallsyms 和包含地址的报告只留本机；本目录不包含内核地址快照。
重启或换内核后，旧符号快照不能直接用于新的采样。
