# 原生与受管保存副本的框架校准（2026-10-13）

先读 [阶段报告](../../TENSOR_CAPACITY_REPORT.md)。三条件各五个独立计时进程，
5预热+100正式，另三组三步诊断；全部随机串行，没有迁移或容量限制。
native不做保存副本，但保留旧runner公共记账和空闲worker。

- diagnostic：数值状态、初始/使用驻留、释放检查，进程节点与匿名映射摘要。
- timing：manifest、整步锚点、完整run、五进程均值与配对t(df=4)区间。
- source：实际执行版本和分析器。
- validation：十项跨矩阵审计拒绝结果及验证源码，原始文件哈希复核。

native/受管DRAM/受管Direct为72.033/111.863/88.291 ms/步。
两条受管路径均比native慢，差值区间大于0；不是纯memcpy或内存延迟测试。
完整步骤、日志、原始numa_maps/smaps保留本机，锚点不足以重算统计。
进程RSS为高水位，节点摘要是诊断时点，不是节点连续峰值。

原始结果：results/framework-baseline-diagnostic-1013-v2、framework-baseline-timing-1013-v1。
首轮diagnostic-1013-v1因共享文件页节点审计口径错误而排除，原数据保留。
