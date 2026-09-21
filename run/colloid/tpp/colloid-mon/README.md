# Colloid/TPP 组件说明

该目录保留上游 Colloid/TPP 运行组件的接口位置。当前仓库的主要复现入口见根目录 [REPRODUCTION.md](../../../../REPRODUCTION.md)；如果需要启用该组件，请先确认对应内核模块、NUMA 参数和 workload 兼容性。

本组件没有纳入当前 CPU DNN + CXL 访问路径实验，不能把加载组件称为完成了收益感知预取验证。
