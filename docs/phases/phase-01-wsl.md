# Phase 01 — WSL / Interop / N-01 根因与修复

日期：2026-09-18
状态：**完成（采用自动恢复方案）**

---

## 1. 问题（N-01）

Kali 冷启动会间歇性清空 WSL2 VM 内**全局共享**的 binfmt_misc 注册表，
删除 Ubuntu 的 29 个 `qemu-*` handler 与 `python3.12`，并短暂删除 `WSLInterop`。

历史影响（来自 docs/PHASE0_PHASE1_EXECUTION_2026-09-17.md）：
Ubuntu 无法调用任何 `.exe`；ARM 透明执行失效；恢复需重启发行版。

## 2. 根因定位（本次实测）

探针：`.p1x-probe/kali-probe.sh`、`.p1x-probe/binfmt-watch.sh`（2 秒级采样）。

### 证据链

1. **注册表是 VM 级共享的**：Ubuntu 与 Kali 挂载的是同一 superblock（dev 0:35，
   mount id 121 全程不变，peer group 相同）。任何一发行版的增删双方可见。
2. **Kali 冷启动时的删除时间线**（两次独立复现）：
   - T+0：Kali systemd-binfmt 运行 → 注册表 33→4（qemu×29 + python3.12 被删）
   - T+16s 左右：4→2（WSLInterop 也被删）
   - Kali 日志：`sh[58]: cannot create /proc/sys/fs/binfmt_misc/WSLInterop: Permission denied`
     —— 这是 WSL 自动生成的 protectBinfmt drop-in 里 `echo -1 > WSLInterop` 失败留下的。
3. **对照实验**：Ubuntu 冷启动是纯增量的（Kali 侧观察到 4→33，未删任何条目）。
   → 破坏性路径是 **Kali 启动特有**，不是 WSL 的通用行为。
4. **间歇性**：5 次 Kali 冷启动，3 次清空、2 次完好。
   昨天文档写"每次必现"——已修正为间歇。
5. **删除是逐条目进行的**，不是 remount（mount id / dev / peer group 全程不变）。

### 机制结论

Kali 启动过程中，其 systemd-binfmt 路径（含 WSL 生成的 drop-in）在共享注册表上
执行了删除操作；由于注册表是 VM 共享的，Ubuntu 的 handler 成为牺牲品。
WSL init 会在**每次新会话启动时**重新注册 WSLInterop（这就是为什么
新开 shell 后 interop 又"自己好了"），但 **qemu/python3.12 没有任何东西会重新注册** ——
这正是 N-01 的真正损害点。

## 3. 修复方案：binfmt-selfheal（自愈）

由于删除发生在 WSL 启动路径内部（不可控、间歇），选择**自动恢复**方案，
满足任务书要求：可自动恢复、可预测、不破坏其他发行版。

### 组件（源码在仓库 configs/ 下）

| 文件 | 安装位置 | 作用 |
|---|---|---|
| `configs/binfmt/binfmt-selfheal.sh` | `/usr/local/sbin/binfmt-selfheal.sh` | 检查并重注册缺失条目（幂等、只增不删） |
| `configs/systemd/binfmt-selfheal.service` | `/etc/systemd/system/` | oneshot 服务，执行脚本 |
| `configs/systemd/binfmt-selfheal.path` | `/etc/systemd/system/` | `PathChanged=/proc/sys/fs/binfmt_misc`，注册表一变即触发 |

工作方式：注册表目录 mtime 变化 → path unit 触发 service → 脚本检查
WSLInterop + 本发行版 binfmt.d 声明的所有条目，缺失则补注册（WSLInterop 用与
WSL 官方 drop-in 完全一致的注册串）。最多重试 5 次，每次间隔 1s。

### 已安装到 Ubuntu，enabled + active。

## 4. 验收结果

**Kali 冷启动回归（最终）**：

```
终止 Kali → 冷启动 → 等 40s 沉降 → 新会话检查:
count=33  WSLInterop=1  qemu=29  python3.12=1
interop: OK  (powershell.exe 从 Ubuntu 执行成功)
ARM64 via qemu binfmt: ARM64 result = 42   (labs/reverse/arm64/hello_arm64)
```

即：**Kali 与 Ubuntu 现在可以共存**，Kali 启动后 Ubuntu 的
interop + QEMU binfmt 均自动恢复，无需人工干预。

## 5. 已知残余风险 / 说明

- flush 是间歇性的，自愈是兜底；若某次 flush 发生在关键操作**进行中**，
  该操作本身可能失败（但随后环境会自愈）。
- WSLInterop 的删除-重注册竞态由 WSL init 负责最终收敛；
  self-heal 的补注册是额外的 belt-and-suspenders。
- Kali 侧未安装 self-heal（Kali 的 binfmt.d 只有 python3.13，
  无 qemu 需求）。如未来 Kali 需要 qemu binfmt，可同样安装。
- 回滚方法：`wsl -d Ubuntu -u root -- systemctl disable --now binfmt-selfheal.path`
  并删除安装的三个文件；仓库 `configs/` 下源码保留。

## 6. 证据文件

- `.p1x-probe/kali-probe-OUTPUT.txt` — Kali 启动时 binfmt 单元取证
- `.p1x-probe/binfmt-watch-OUTPUT.txt` — 2s 级注册表采样（含 flush 时间线）
- `.p1x-probe/binfmt-watch-KALI-OUTPUT.txt` — Kali 侧对照采样
- `.p1x-probe/wslinterop-lifetime-OUTPUT.txt` — WSLInterop 删除/重注册存活实验
- `.p1x-probe/svc-vs-shell-OUTPUT.txt` — systemd 服务 vs shell 写权限对照
- `.p1x-probe/n01-acceptance-OUTPUT.txt` / `-acceptance2-OUTPUT.txt` — 验收
