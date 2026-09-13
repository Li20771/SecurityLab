# ARM64 ELF 基础实验

## 实验目的

验证 Ubuntu 下 ARM64 程序从交叉编译、QEMU 执行，到 GDB-Multiarch 远程动态调试的完整基础流程。

## 实验环境

- Ubuntu 24.04
- GCC 13.3.0
- AArch64 GCC 13.3.0
- QEMU 8.2.2
- GDB-Multiarch 15.1

## 实验文件

- `hello_arm64.c`：ARM64 C 源代码
- `hello_arm64`：交叉编译生成的 ARM64 ELF 程序

## 1. AArch64 交叉编译

使用 AArch64 交叉编译器：

~~~bash
aarch64-linux-gnu-gcc -g -O0 -static -o hello_arm64 hello_arm64.c
~~~

参数说明：

- `-g`：加入调试信息
- `-O0`：关闭优化，方便调试
- `-static`：生成静态链接程序，方便直接使用 QEMU 运行
- `-o hello_arm64`：指定输出文件

## 2. ELF 架构检查

使用：

~~~bash
file hello_arm64
readelf -h hello_arm64
~~~

实验结果：

~~~text
ELF 64-bit LSB executable
ARM aarch64
statically linked
with debug_info
not stripped
~~~

`readelf` 确认：

~~~text
Class:   ELF64
Type:    EXEC
Machine: AArch64
~~~

## 3. QEMU 执行

直接使用 QEMU-AArch64：

~~~bash
qemu-aarch64 ./hello_arm64
~~~

程序输出：

~~~text
ARM64 result = 42
~~~

说明 ARM64 ELF 可以在当前 Ubuntu 环境中通过 QEMU 正常运行。

## 4. QEMU + GDB-Multiarch 动态调试

首先启动 QEMU 的 GDB Stub：

~~~bash
qemu-aarch64 -g 1234 ./hello_arm64
~~~

然后在另一个终端启动 GDB-Multiarch：

~~~bash
gdb-multiarch ./hello_arm64
~~~

进入 GDB 后：

~~~text
set architecture aarch64
target remote :1234
break add
continue
~~~

成功连接后，程序可以在 `add()` 函数处停止。

## 5. ARM64 寄存器观察

程序进入：

~~~text
add(a=10, b=32)
~~~

观察到：

~~~text
x0 = 10
x1 = 32
~~~

当前 ARM64 调用约定下，前两个整数参数通过 `x0` 和 `x1` 传递。

## 6. ARM64 指令单步

在 `add()` 函数中观察到：

~~~asm
add w0, w1, w0
~~~

执行前：

~~~text
w0 = 32
w1 = 10
~~~

执行指令后：

~~~text
w0 = 42
~~~

对应：

~~~text
32 + 10 = 42
~~~

由于 ARM64 的整数返回值通过 `x0` 返回，因此最终：

~~~text
x0 = 42
~~~

## 7. 实验流程总结

本实验完整验证了：

~~~text
C 源码
  ↓
AArch64 交叉编译
  ↓
ARM64 ELF
  ↓
QEMU-AArch64
  ↓
GDB-Multiarch
  ↓
QEMU GDB Stub
  ↓
断点
  ↓
ARM64 寄存器
  ↓
单步执行
  ↓
返回值
~~~

## 实验结论

ARM64 基础逆向实验环境已经验证成功。

当前环境已经可以用于：

- ARM64 ELF 分析
- QEMU 用户态仿真
- GDB-Multiarch 调试
- ARM64 寄存器分析
- ARM64 指令单步分析
- 后续 ARM 固件动态分析

## 后续方向

在本实验基础上继续学习：

1. ARM64 调用约定
2. ARM64 栈帧
3. ARM64 函数调用
4. ARM64 汇编
5. ELF 装载与动态链接
6. QEMU 用户态仿真
7. 固件文件系统分析
8. ARM 固件动态调试
9. ECU 程序分析
