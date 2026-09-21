# 主机 ALTO 内核准备说明

本文档记录候选内核的构建和安装保护流程。除非明确执行安装脚本，否则不会安装内核、修改 GRUB、卸载驱动或重启服务器。

## 源码与配置

- Ubuntu 基线：6.8.0-138，源码基于 Linux 6.8.12。
- 补丁：`src/alto/nbt/nbt-alto-ubuntu-6.8.0-138.patch`。
- 候选版本：`6.8.12-138-soaralto`。
- 源码目录：`kernel/ubuntu-jammy-6.8.0-138/`。

该补丁保留 Ubuntu 的迁移返回值，并增加 ALTO 扫描比例 sysctl。候选内核未使用 Canonical 签名；Secure Boot 环境不能直接假定可启动。

## 构建

```bash
bash run/prepare_build_dependencies.sh --with-host
bash run/build_host_alto_kernel.sh
bash run/build_host_nvidia.sh
```

输出位于 `kernel/build-host-alto/` 和 `kernel/stage-host-alto/`，只是 staging 文件，不等于已经安装。NVIDIA 脚本只在候选内核源码上编译模块，不改变系统 DKMS 状态。

## 安装前检查

先完成完整构建、模块审计和 VM 检查：

```bash
KERNEL_IMAGE="$PWD/kernel/build-host-alto/arch/x86/boot/bzImage" \
VM_LOG="$PWD/results/host-alto-vm.log" bash run/test_alto_vm.sh
```

物理机安装前必须确认 initramfs 包含存储、网卡、CXL/DAX、NUMA 和 NVIDIA 所需模块，并保存当前 GRUB、内核和模块版本。当前训练实验使用的内核已经是 `6.8.12-138-soaralto`；继续实验前仍需读取 live 状态。

## 与 CPU 训练研究的关系

该内核为 Direct CXL/Prefetch 实验提供环境，但 v3 方案首轮不要求重新编译或重启，也不把“加载 ALTO 内核”当作运行了 ALTO 策略。ALTO 在线扫描属于后续可选消融。
