# C 内计时与独立调用证据（2026-09-29）

训练六条件共 18 个计时进程、1800 个步骤；另有六组正确性诊断。独立调用三个进程，
每进程 1/8/32 MiB 同节点及跨节点各 12 次正式调用，共 216 次。
保存摘要、CSV、命令、配置、诊断事件、回归日志、C 源码和二进制哈希；实际二进制仅留本机。
index.json 列出各运行文件的来源、大小、SHA256 和是否归档。完整 steps.json 和
samples.json 保留在服务器 results/migration-native-*-0929/，归档包含其哈希。
所有汇总时间为 ms，原始 *_ns 为纳秒；C 内时间不是纯硬件传输时间。

source/ 的 build_native_meter.py 和 run_native_isolation_matrix.py 是实验完成后新增的
复现入口；不声称它们就是本轮最初执行的构建/矩阵启动脚本。实际版本以 manifest
及 meter-build.json 为准。归档中的 profile_native_migration_kernel.sh 是当时版本；
后续 sudo 采样已完成，详见 ../migration-kernel-profile-0929/README.md。
当前采样脚本及修正报告以仓库 run/ 和后续内核采样归档为准。内核符号不公开提交。

结果、确定性边界和完整 sudo 命令见 [实验报告](../../NATIVE_MIGRATION_REPORT.md)。
