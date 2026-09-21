# E1 异步机制与 E2 首轮探索结果

日期：2026-09-21。定位：容量充足的执行器/调度问题验证，不是收益策略有效性证据。

## 已执行

- 新增 `access_paths.py`，独立 mmap 副本、单后台工作者、Future 异常传播、
  需求等待、重复 unpack 只迁一次、结束时等待所有任务和引用释放。
- `run_access_paths.py` 分开 diagnostic 与 timing：计时模式不做逐页查询、
  梯度/参数快照或每步 GC；保留 pack 复制、hooks、迁移/等待、优化器、
  drain 队列屏障和必要记录成本。输出文件在计时区间外写入。
- 计算使用 CPU 0–7，工作者固定 CPU 8；所有模式均保留相同工作者资源。
  用 numactl 将非受管匿名分配绑定 DRAM 0。受管副本独立绑定 DRAM 0 或 CXL 2。
- async 策略在 pack 完成后立即提交，单工作者 FIFO；没有未来需求 oracle、
  没有额外 sleep/计算窗口，没有容量上限或逐对象收益选择。

两层显式 matmul/softmax Transformer，width=128、heads=4、FP32、SGD，
small 为 batch=4/sequence=128，large 为 batch=8/sequence=512。
四策略为受管全 DRAM、Direct CXL、需求时同步迁移、立即提交的异步迁移。
受管全 DRAM 不是原生 DRAM-only；本轮未测试全进程 CXL-only。

诊断矩阵共 8 次运行，各 3 步，与相同初始化的 native 逐步对比 loss、梯度、
更新后参数，最大误差均为 0，所有受管映射释放。初始及使用时逐页位置检查
通过；不同策略的候选序列一致，源码快照 hash 校验通过。

异步诊断中，每档 105 次迁移有 102 次在需求到达前完成；迁移执行区间与
前向阶段重叠。这证明后台执行和提前完成，前向阶段重叠量不等同于纯算子
计算重叠量，也不代表隐藏了全部成本。

## 首轮计时

共 40 个独立进程：2 档 × 4 策略 × 5 重复，策略顺序随机化，每进程预热 5 步、
计时 20 步。以下为 5 个独立进程均值的平均，单位 ms/step：

| 规模 | 受管 DRAM | Direct CXL | 同步迁移 | Eager FIFO async |
| --- | ---: | ---: | ---: | ---: |
| small | 13.195 | 10.417 | 19.209 | 17.649 |
| large | 113.331 | 90.453 | 181.648 | 174.098 |

Direct minus async 的配对差值与探索性 95% t 区间（5 对独立进程，df=4）：

- small：−7.232 ms，区间 [−9.235, −5.229] ms。
- large：−83.645 ms，区间 [−86.240, −81.049] ms。

每步成功迁移字节：small 10.789 MiB、large 134.156 MiB。
异步需求等待平均分别为 1.546 ms、46.197 ms；每步平均仅一个迟到 unpack。
这说明“迟到对象比例低”不能独立证明调度好：一个关键等待就可能阻塞反向。
这些迁移字节来自成功页状态，不是 CXL 总线总流量，尚未采集 PMU。

## 发现的问题与处理

1. Future 完成早于线程释放 work item 的引用。在途释放测试发现短暂残留；
   增加队列屏障，所有策略在计时内支付同样收尾屏障成本。三项 async 测试
   验证重复 unpack、注入工作者错误、在途任务持有并最终释放映射。
2. **FIFO 存在需求顺序倒置，尚待调度优化。** 诊断第一步的提交对象 ID 以
   2、4、5、6、8 开始，需求却以 56、54、50、52、53 开始。迟到的 ID 56 在
   small 排队约 7.157 ms、自身迁移仅 0.206 ms；large 排队约 33.731 ms、
   自身迁移约 1.670 ms。这些来自详细诊断，不与低开销计时的等待值混用。
3. Direct 在此副本机制下也快于受管全 DRAM。可能涉及前向复制、分层带宽、
   缓存及内存分配差异，现有数据无法归因；不能解释为 CXL 介质本身快于 DRAM。
   需要拆分阶段、拷贝/放置成本和后续 PMU，再解释这个现象。

因此当前 async 是已测的简单机制基线，**不是充分优化的 Always-Prefetch，
更不是 TierTrain**。不据此宣称“预取总是无益”“所有 tensor 应 Direct”或
“选择性预取有效”。尚未做单 tensor 干预，也尚未找到预取胜出的对象/上下文。
Q1–Q3 仍未通过。

## 下一步实验门槛

先记录早期迭代的实际需求顺序，下一迭代按预测期限选择就绪任务，避免 FIFO
把首个需求排在所有迁移之后；探索/校准开销计入首次与摊销时间，不使用未来
实测需求作弊。对比立即提交与按需求/窗口触发，校验在途异常和释放，再在
同资源下重跑强预取基线。

同时解释 Direct 与受管 DRAM 的差异。之后才做网络中单对象 Direct/Prefetch
干预及两种赢家的独立确认；若仍只有 Direct 获胜，按计划保留负结论，暂不
进入收益策略或 ML。AOL/PMU 建模不替代这些因果检查。

## 文件与复跑

Git 中已归档：[诊断证据](evidence/e1-diagnostic/)、
[计时与图表](evidence/e2-timing/)、[归档说明](evidence/README.md)。
服务器原始结果目录见下文；归档日志后缀改为 `.log.txt`，内容保留原样。

从仓库根目录运行，输出目录必须不存在：

```bash
python3 research/cpu_training/run_path_matrix.py results/e1-new --phase diagnostic
python3 research/cpu_training/run_path_matrix.py results/e2-new --phase timing --repeats 5
python3 research/cpu_training/analyze_paths.py results/e2-new
python3 research/cpu_training/plot_paths.py results/e2-new
python3 research/cpu_training/test_async.py
```

使用现有项目内训练依赖；绘图复用 `.deps/python` 的 matplotlib，与训练依赖隔离。
`analyze_paths.py` 目前按 n=5 的 t 临界值设计，其他重复数应显式调整统计方法。

- 诊断：`results/cpu-training-e1-diagnostic/`。
- 计时：`results/cpu-training-e2-timing/`。
- 关键文件：`commands.json`、`completed.json`、`process-means.csv`、`summary.json`、
  `path-comparison.png/pdf`、`environment-observation.json`、`regression-tests.log`。
- 每运行：源码快照、manifest/依赖、`steps.json`、stdout/stderr；诊断另含
  `correctness.json` 与 `events.json`。失败保留 traceback。

实验期间全局设置未修改：numa_balancing=1、pte_scale=16、perf_event_paranoid=4、
demotion=false。采用受管 MPOL_BIND 保护路径，未启动在线 ALTO 控制。
本轮仅确认固定形状、非原地 workload；弱引用版本检测并不是完整别名分析。
