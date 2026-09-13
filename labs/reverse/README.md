# Reverse Engineering Labs

这里用于保存 SecurityLab 的逆向工程与二进制分析实验。

## 当前实验

### 01 - x86-64 ELF 基础
目录：

`hello/`

内容：

- C → ELF 编译
- file
- readelf
- nm
- objdump
- GDB
- Pwntools
- x86-64 寄存器与函数参数
- GDB 断点与单步

### 02 - ARM64 ELF 基础
目录：

`arm64/`

内容：

- AArch64 交叉编译
- ARM64 ELF
- QEMU-AArch64
- GDB-Multiarch
- QEMU + GDB 远程调试
- ARM64 寄存器
- ARM64 函数调用与返回值

## 环境

主要工具：

- GCC / G++
- Binutils
- GDB
- GDB-Multiarch
- Pwntools
- Capstone
- ROPgadget
- Unicorn
- QEMU

## 实验原则

1. 实验优先使用隔离的本地样本。
2. 编译产物默认不提交 Git。
3. 固件、PCAP、大型二进制文件按照 SecurityLab 的数据存储规则管理。
4. 每个实验应记录：
   - 实验目的
   - 环境
   - 操作步骤
   - 关键结果
   - 分析结论
