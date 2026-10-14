# 原生阶段缺页与算子诊断证据（2026-10-14）

固定当前FP32 Transformer、默认CPU allocator、进程匿名DRAM0/CXL2放置。
先两节点各3个CPU profiler进程，仅最后10步记录；再各5个无profiler独立进程。
所有进程5预热+100正式，无保存副本、无迁移、无DRAM容量上限。

无采样DRAM/CXL为76.803/90.470 ms/步，配对差+13.667 ms [4.869,22.465]。
backward五轮均慢，四轮CXL缺页次数反而更少。CPU profiler候选是反向bmm、
softmax backward及mm；采样时间与内存账目不是端到端因果贡献或完整内存峰值。
详见 [阶段报告](../../NATIVE_OPERATOR_REPORT.md)。

| 目录 | 内容 |
|---|---|
| diagnostic/ | 六个采样进程配置、阶段计数、算子/输入形状self时间摘要 |
| timing/ | 十个无采样进程均值、配对区间、阶段缺页、loss与最终状态hash |
| source/ | 实际执行的runner/analyzer及依赖源码，审计与归档源码 |
| validation/ | 十项错误输入拒绝结果、计时后测试时钟、239原始hash复核 |

[raw-checksums.json](raw-checksums.json) 包括未复制进归档的完整operator-events.json
与trace.json。完整约70 MiB原始数据留本机results/native-operator-{profile,timing,validation}-1014-v1，
不覆盖/删除。步骤锚点不能代替原始统计；CPU profiler不能给出LRU锁墙钟占比。
