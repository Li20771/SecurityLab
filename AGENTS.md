# SecurityLab Agent 工作规范

## 1. 项目定位

SecurityLab 是个人网络安全与车联网安全研究环境。

主要方向：

- 网络安全
- 二进制逆向
- 固件分析
- ARM / AArch64
- Linux / Embedded Linux
- CAN / SocketCAN
- ISO-TP
- UDS
- 车联网安全
- 后续漏洞研究与自动化实验

当前核心目录：

- labs/reverse/     逆向工程实验
- labs/firmware/    固件分析实验
- labs/vehicle/     车联网安全实验
- configs/          可复现的环境配置

---

## 2. Agent 工作目标

Agent 的主要职责：

1. 理解和维护 SecurityLab 项目结构。
2. 协助分析代码、脚本、固件、ELF、配置文件和实验结果。
3. 协助建立和维护安全研究实验环境。
4. 尽可能优先使用可复现、可验证的方法。
5. 在执行修改前说明目的、影响范围和预期结果。
6. 任何不确定的危险操作都应先停下来请求用户确认。

---

## 3. 工作区

默认工作区：

/mnt/d/SecurityLab

除非用户明确指定，否则不要把项目文件写到工作区之外。

---

## 4. 默认允许的操作

以下操作通常可以直接执行：

- 读取项目文件
- 搜索代码和文本
- 查看目录结构
- 查看 Git 状态
- 查看 Git log
- 查看 Git diff
- 运行只读分析命令
- 运行测试
- 编译实验程序
- 在 labs/ 下创建实验文件
- 在实验目录中修改代码和配置
- 分析 ELF、固件和网络数据
- 使用本地安全实验环境进行测试

---

## 5. 必须等待用户明确指令的操作

以下操作必须等待用户明确要求：

- git add
- git commit
- git push
- git reset --hard
- git clean
- 大规模删除文件
- 删除实验目录
- 修改 Git 历史
- 修改远程仓库
- 修改 GitHub 内容
- 修改系统级配置
- sudo 安装或删除系统软件
- 修改 /etc 下的关键配置
- 修改 WSL 全局配置
- 修改 Docker Desktop 全局配置
- 修改真实网络接口
- 修改真实 CAN 接口
- 连接真实 ECU
- 向真实车辆或真实 ECU 发送诊断或控制报文

---

## 6. Git 安全规则

Git 操作遵守以下原则：

### 可以直接执行

- git status
- git status -sb
- git log
- git diff
- git show
- git branch
- git remote -v

### 必须用户明确要求

- git add
- git commit
- git push
- git reset
- git clean
- git rebase
- git merge

任何可能改变提交历史的 Git 操作，都必须先说明影响并等待用户确认。

---

## 7. 删除文件规则

不要因为“看起来没用”而删除文件。

删除文件之前：

1. 确认文件用途。
2. 检查 Git 状态。
3. 说明删除原因。
4. 对项目文件等待用户明确授权。

对于实验生成物，也优先确认是否属于可重新生成的产物。

---

## 8. 系统修改规则

系统级修改必须谨慎。

涉及以下目录时，默认视为系统级操作：

- /etc/
- /usr/
- /var/
- /boot/
- /lib/
- /lib64/
- /opt/

涉及 sudo 的操作，在没有明确要求时不要执行。

---

## 9. 真实车辆安全边界

默认所有车联网实验均使用：

- vcan0
- 虚拟 ECU
- 模拟 CAN 总线
- 本地测试环境
- 用户明确提供的实验设备

不要主动连接真实车辆、真实 ECU 或真实 CAN 适配器。

不要向真实车辆发送：

- CAN 控制报文
- UDS 诊断请求
- 编程/刷写请求
- 安全访问请求
- ECU 重置请求

除非用户明确说明目标设备、测试范围和授权环境。

---

## 10. 固件分析规则

固件分析优先使用：

- binwalk
- file
- strings
- readelf
- objdump
- nm
- hexdump / xxd
- GDB
- GDB-Multiarch
- QEMU
- Ghidra
- Python

分析流程优先：

固件
→ 文件系统识别
→ 提取
→ rootfs
→ ELF 搜索
→ 架构识别
→ 依赖分析
→ 配置分析
→ 启动流程分析
→ 静态分析
→ 动态分析

不要因为发现可疑文件就直接修改原始样本。

---

## 11. 实验文件规则

实验内容优先放在：

labs/

生成物与源文件分离。

大型固件、提取目录、临时编译产物原则上不要直接提交 Git。

如果不确定某个文件是否应该进入 Git：

- 先检查 .gitignore
- 检查 git status
- 不要擅自修改 Git 策略

---

## 12. 环境配置规则

可复现的环境配置优先保存到：

configs/

例如：

configs/systemd/vcan0.service

不要把密码、token、SSH 私钥、API Key、Cookie 或其他凭据写入 Git。

不要读取并输出：

~/.dsh/.credentials.yaml

等敏感凭据文件的内容。

---

## 13. DSH 相关规则

当前 DSH 使用 SecurityLab 作为 Workspace。

Agent 可以：

- 读取 SecurityLab
- 分析代码
- 分析实验
- 运行测试
- 修改用户授权范围内的实验文件

Agent 不应自行：

- git add
- git commit
- git push
- git reset --hard
- git clean
- 删除大量文件
- 修改系统配置

当任务可能破坏已有实验时：

先检查 Git 状态，
再说明计划，
再执行。

---

## 14. 执行命令的原则

优先：

1. 小范围操作
2. 可验证操作
3. 可回滚操作
4. 先检查后修改
5. 每一步完成后验证结果

避免一次执行大量不可逆命令。

---

## 15. 报告原则

执行环境操作后，应清楚说明：

- 执行了什么
- 为什么执行
- 得到了什么
- 是否修改了文件
- 是否修改了系统
- Git 状态是否变化
- 下一步是什么

不要把猜测当成事实。

---

## 16. 用户优先

当用户的明确要求与默认工作规范冲突时：

- 先指出冲突
- 说明风险
- 等待用户明确确认

任何涉及不可逆修改、真实设备、真实车辆或远程仓库的操作，都优先保护用户当前环境。
