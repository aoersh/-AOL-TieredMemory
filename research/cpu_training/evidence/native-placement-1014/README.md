# 默认分配器原生节点对照证据（2026-10-14）

两条件DRAM0/CXL2，各五个独立计时进程，另各一个三步诊断进程。
默认PyTorch CPU allocator，无保存副本、无迁移、无额外worker；
进程导入前设置MPOL_BIND，含张量之外的匿名分配，没有DRAM额度限制。

原生DRAM/CXL为74.245/89.284 ms/步，配对差+15.039 ms [6.046,24.032]。
完整训练状态和驻留诊断通过；五轮方向一致但幅度变化，不能当固定硬件延迟差。
详见 [阶段报告](../../NATIVE_PLACEMENT_REPORT.md)。

| 目录 | 内容 |
|---|---|
| diagnostic/ | 两条件配置、三步状态hash、存储节点覆盖摘要、进程匿名驻留摘要 |
| timing/ | 五配对进程均值、区间、启动命令、计时协议及首/预热后/末步锚点 |
| source/ | 实际执行的runner、analyzer及依赖源码，审计/归档源码 |
| validation/ | 九项错误输入拒绝结果及155个原始文件hash复核记录 |

完整原始结果在本机results/native-placement-{diagnostic,timing,validation}-1014-v1。
两矩阵的有效分析均为各自verified-analysis。原始文件保留，hash清单见
[raw-checksums.json](raw-checksums.json)。步骤锚点不能重新生成完整统计；
地址、完整内存映射和日志不进入轻量归档。源码快照是历史证据，不是新实验入口。
