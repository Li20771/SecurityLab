# Binwalk 固件分析基础实验

## 实验目的

通过制作一个最小的 SquashFS 文件系统，模拟一个简单的固件镜像。

使用 Binwalk 完成：

- 固件文件系统识别
- SquashFS 识别
- 文件系统自动提取
- 提取后的 rootfs 检查

本实验用于理解真实固件分析的第一步：

固件文件
  ↓
Binwalk 识别
  ↓
发现文件系统
  ↓
提取文件系统
  ↓
进入 rootfs 分析

## 实验目录

当前实验目录：

labs/firmware/binwalk-basic/

目录结构：

binwalk-basic/
├── README.md
├── rootfs/
│   └── etc/
│       ├── version
│       └── config.txt
├── demo_firmware.squashfs
└── extracted/
    └── _demo_firmware.squashfs.extracted/
        ├── 0.squashfs
        └── squashfs-root/
            └── etc/
                ├── config.txt
                └── version

## 1. 制作模拟 rootfs

创建：

rootfs/etc/version
rootfs/etc/config.txt

version 文件内容：

SecurityLab Firmware Lab

config.txt 文件内容：

device=ecu-demo
model=demo-ecu

## 2. 制作 SquashFS 固件

使用命令：

mksquashfs \
  rootfs \
  demo_firmware.squashfs \
  -noappend

生成：

demo_firmware.squashfs

使用 file 检查：

file demo_firmware.squashfs

结果确认：

- SquashFS filesystem
- version 4.0
- little endian
- zlib compressed

## 3. 使用 Binwalk 扫描

执行：

binwalk demo_firmware.squashfs

Binwalk 成功识别：

Squashfs filesystem
little endian
version 4.0
compression:gzip

说明 Binwalk 能够正确识别固件中的 SquashFS 文件系统。

## 4. 使用 Binwalk 自动提取

创建提取目录：

mkdir -p extracted

执行：

binwalk -e -C extracted demo_firmware.squashfs

最终得到：

extracted/
└── _demo_firmware.squashfs.extracted/
    ├── 0.squashfs
    └── squashfs-root/
        └── etc/
            ├── config.txt
            └── version

## 5. 检查提取结果

读取：

cat extracted/_demo_firmware.squashfs.extracted/squashfs-root/etc/version

结果：

SecurityLab Firmware Lab

读取：

cat extracted/_demo_firmware.squashfs.extracted/squashfs-root/etc/config.txt

结果：

device=ecu-demo
model=demo-ecu

说明 Binwalk 已经成功把固件中的 SquashFS 文件系统提取出来。

## 实验结论

本实验完整验证了：

rootfs
  ↓
SquashFS
  ↓
模拟固件
  ↓
Binwalk 扫描
  ↓
识别文件系统
  ↓
自动提取
  ↓
squashfs-root
  ↓
读取配置文件

这套流程是后续真实嵌入式固件分析的基础。

## 后续分析方向

后续进入真实固件分析时，需要继续学习：

1. 固件整体结构识别
2. Linux rootfs 分析
3. /etc 配置分析
4. 启动脚本分析
5. ELF 文件寻找
6. 网络服务识别
7. 硬编码凭据搜索
8. SUID/SGID 程序分析
9. ARM/AArch64 ELF 分析
10. QEMU 固件仿真
11. GDB 动态调试
12. 固件漏洞定位
