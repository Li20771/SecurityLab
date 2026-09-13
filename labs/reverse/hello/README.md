# x86-64 ELF 基础实验

## 实验目的

验证 Ubuntu 下从 C 源码编译 ELF，到静态分析和动态调试的完整基础流程。

## 实验环境

- Ubuntu 24.04
- GCC 13.3.0
- Binutils 2.42
- GDB 15.1
- Pwntools 4.15.0

## 实验文件

- `hello.c`：C 源代码
- `hello`：编译生成的 ELF 程序

## 1. 编译

使用 GCC 编译：

~~~bash
gcc -g -O0 -o hello hello.c
~~~

参数说明：

- `-g`：加入调试信息，方便 GDB 调试
- `-O0`：关闭编译优化，方便观察源码与汇编的对应关系
- `-o hello`：指定输出文件名为 `hello`

## 2. ELF 基础分析

使用以下工具分析 ELF：

~~~bash
file hello
readelf -h hello
nm hello
objdump -d -M intel hello
~~~

主要观察：

- ELF 文件类型
- CPU 架构
- 程序入口地址
- 符号表
- 函数地址
- 汇编指令

本实验编译得到：

- ELF 64-bit
- x86-64
- PIE 可执行文件
- 包含调试信息
- 未剥离符号

主要函数地址：

- `add`：`0x1149`
- `main`：`0x1161`

## 3. GDB 动态调试

使用 GDB 对 `add()` 函数进行动态调试。

通过断点进入：

~~~text
add(a=10, b=32)
~~~

实验中观察到：

~~~text
RDI = 10
RSI = 32
~~~

这说明在当前 x86-64 Linux 调用约定下，前两个整数参数通过 `RDI` 和 `RSI` 传递。

随后对 `add()` 函数进行单步执行，可以观察到加法指令完成：

~~~text
10 + 32 = 42
~~~

最终程序正常退出，并输出：

~~~text
result = 42
~~~

## 4. Pwntools

使用 Pwntools 读取 ELF：

~~~python
from pwn import ELF

e = ELF("./hello", checksec=False)
~~~

实验结果：

~~~text
Architecture: amd64
Bits: 64
PIE: True
add: 0x1149
main: 0x1161
~~~

说明 Pwntools 可以正确识别当前 ELF 的：

- CPU 架构
- 位数
- PIE 属性
- 函数符号地址

## 5. 实验流程总结

本实验完整验证了：

~~~text
C 源码
  ↓
GCC 编译
  ↓
ELF 文件
  ↓
file / readelf / nm / objdump
  ↓
GDB 动态调试
  ↓
Pwntools ELF 分析
~~~

## 实验结论

x86-64 ELF 的基础分析与动态调试环境已经验证成功。

当前环境已经可以用于：

- ELF 基础分析
- 函数与符号分析
- 汇编代码观察
- GDB 断点调试
- 寄存器分析
- Pwntools ELF 操作
- 后续 CTF Pwn 学习

## 后续学习方向

1. 栈与栈帧
2. x86-64 调用约定
3. 函数调用与返回
4. 内存布局
5. PIE / NX / Canary / RELRO
6. 栈溢出
7. ROP
8. Pwntools 自动化利用
