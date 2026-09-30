# 非目标 DRAM 缓冲复用证据（2026-09-30）

主报告：[BUFFER_REUSE_REPORT.md](../../BUFFER_REUSE_REPORT.md)。

- diagnostic：四组三步数值/逐页驻留/释放诊断，误差全为 0。
- timing：四条件各三次独立进程，5 预热 + 100 正式，随机区组种子 20260930。
- timing/verified-analysis/summary.json：进程均值、配对 t(df=2) 区间及差中差。
- source：执行及分析源码，不包含 C 库二进制。
- validation：32 项回归日志和命令；六类破坏记录被分析器拒绝的结果。
- raw-checksums.json：服务器原始文件哈希；完整步骤与二进制仍在 results/。
- step-anchors.json：每进程首/中/末正式步，以及首个预热步，非完整步骤替代品。
- cxl-topology-sanitized.json：设备/node 关系，不包含 resource/start 物理地址。

原始目录：results/buffer-reuse-{diagnostic,timing,validation}-0930-v1/。
初始化、每步状态重置与池关闭已记账。只复用非目标 node0 副本，ID10 每步
在 node2 新建；Prefetch 每步迁移 32 MiB，Direct 不迁移；没有隐去目标回迁。

结果：fresh Direct/Prefetch 104.743/133.156 ms；reuse 88.560/107.460 ms。
预取额外成本降低 9.513 ms [4.581,14.445]，但 reuse Prefetch 仍慢
18.900 ms [12.946,24.854]。支持分配实现干预收益，未单独量化 LRU 自旋贡献。
本批没有新内核 profile；09-29 原始数据与有效分析均保留。

分析器核对所有原始步骤、loss 序列、选择记录、源码/二进制哈希、迁移量及计时等式；
诊断 verified-analysis 保留首次执行的分析器版本，计时分析器额外报告外层进程墙钟。
早期 results/reuse-pool-diagnostic-0930/ 属开发冒烟检查，不纳入正式比较。
