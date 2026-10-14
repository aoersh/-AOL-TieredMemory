# 无保存副本的C10张量存储容量筛查（2026-10-13）

先读 [阶段报告](../../TENSOR_CAPACITY_REPORT.md)。七条件各三个独立计时进程，
5预热+100正式，另七组三步诊断；全部随机串行，没有迁移或saved副本复制。

配额覆盖安装后经C10 CPU allocator分配的参数、输入、梯度、源张量和临时存储。
解释器、库内部直接分配和安装前内存不在范围内，不是整进程DRAM限额。
通过PyTorch现有allocator接口实施，节点0/2，页对齐预约，storage释放且munmap后退款。

- diagnostic：原生状态hash、覆盖摘要、所有活跃张量逐页审计后的容量/驻留摘要。
- timing：manifest、步骤锚点、完整run、三进程均值与配对t(df=2)区间。
- source：实际执行Python/C++和分析版本。
- validation：五项allocator行为检查、十项跨矩阵审计拒绝、构建命令/ABI/源码、原始文件哈希。

DRAM张量峰值178.402 MiB；first64/128不超额，末端全部分配释放。
first64−全部CXL为−4.251 ms [−11.450,2.949]，未证明稳定实用收益；同预算规则
也无稳定赢家。新allocator全DRAM比默认原生慢52.089 ms [40.408,63.771]，
其逐次mmap分配成本仍显著，停止扩展迁移/AOL。本批与框架校准不拼接绝对耗时。

完整步骤、全部分配记录与numa_maps/smaps、日志、构建二进制留在本机results。
诊断遇零页查询EFAULT时在有效映射上原值写回后重查并记录触页数；正式计时无此操作。
锚点和摘要不足以重算全部统计。覆盖字节是观测累加，不是唯一storage或峰值。

原始结果：results/tensor-capacity-{diagnostic,timing}-1013-v1。
构建：results/tensor-capacity-build-1013-v1；行为检查：tensor-capacity-validation-1013-v2。
validation-1013-v1因零页查询审计边界失败，原数据保留，不作通过证据。
