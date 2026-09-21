# AOL 指标

## 定义

AOL 使用以下两个 Granite Rapids PMU 事件计算：

```text
OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD /
OFFCORE_REQUESTS.DEMAND_DATA_RD
```

它表示内存请求的平均等待特征，不能直接当作秒数、迁移预算或张量收益。

## 测量

```bash
perf stat -e OFFCORE_REQUESTS.DEMAND_DATA_RD,OFFCORE_REQUESTS_OUTSTANDING.CYCLES_WITH_DEMAND_DATA_RD <command>
```

事件是否可用取决于 CPU 型号、perf 权限和事件配置。实验报告中必须记录 event running、复用和缺失情况。
