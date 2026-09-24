# scripts/repro —— 关键验证的可复现入口（reproducibility entrypoints）

审计发现：很多重要验证确实执行过，但证据主要在 `tmp/audit/`（已
gitignore、不进版本控制），导致"文档说 VERIFIED，但换一台机器无法只靠
repo 重现"。本目录把这些验证固化为版本化的最小可复现脚本。

设计约定：

- 每个脚本：检查依赖 → 执行最小可重复实验 → 输出关键结果 → 明确退出码；
- 产物全部写入 `mktemp` 临时目录，脚本结束即删除——**不污染真实系统、
  不提交生成物**；
- 退出码语义统一：`0` = 全过；`1` = 验证失败；`2` = 必需依赖缺失
  （环境不可用，与"验证失败"严格区分）。

| 脚本 | 验证内容 | 必需依赖 | 耗时 |
|------|----------|----------|------|
| `verify_arm.sh` | AArch64 编译+执行、armhf 编译、qemu-arm 执行 | aarch64/armhf 交叉 gcc、qemu-arm（qemu-aarch64 仅 binfmt 不可用时需要） | 秒级 |
| `verify_qemu_gdb.sh` | qemu-aarch64 -g ↔ gdb-multiarch remote：断点命中→continue→exit 0 | 交叉 gcc、qemu-aarch64、gdb-multiarch | ~10s |
| `verify_ghidra.sh` | **AArch64** ELF → analyzeHeadless 分析 → Architecture=AARCH64 → ERROR_COUNT=0 → main 反汇编 12 条（postScript `ghidra_scripts/DumpDisasm.java`） | aarch64-linux-gnu-gcc、Ghidra（`GHIDRA_BIN` 可覆盖，默认 `/opt/ghidra/support/analyzeHeadless`） | 1-3 分钟 |
| `verify_fuzz_smoke.sh` | fuzz harness + case schema + 分类 + kernel oracle 三态 + replay（3 case） | vcan0 UP（**独占**）、venv 含 python-can/can-isotp（`PYTHON` 可覆盖） | ~30s |
| `verify_uds_fuzz_smoke.sh` | **UDS 请求层** fuzz 最小闭环（v1 SF + OQ3 MF）：离线 10 项契约检查（size-class 边界 7/8/128 合法、129/200/300 **admission reject**、M1–M7 逐项传输语义、no-op/dedup、v1 golden **947** case + 2 个冻结 digest）+ 固定 `--seed 12345 --max-cases 3` 的 vcan campaign（1× SF / 1× canonical MF / 1× malformed MF，含 **MF basic** 8 字节 FF+CF 真打 ECU）+ replay | vcan0 UP（**独占**）、venv 含 python-can/can-isotp（`PYTHON` 可覆盖） | ~1-2 分钟 |
| `verify_gateway_runtime.sh` | **OQ7 Gateway Runtime v1** 最小闭环：启动 `gateway_runtime.py` 守护进程 → 13 项端到端断言（物理寻址三对 CAN ID 且原始抓包证明不串号、多帧响应 FF+CF、功能寻址 0x7DF 三 ECU 都执行而**总线零响应帧**、未知 ID 总线只有 tester 自己的请求、畸形请求回 NRC 且进程存活）→ SIGTERM 优雅退出（退出码 0）+ 无残余进程 | vcan0 UP（**独占**）、venv 含 python-can/can-isotp（`PYTHON` 可覆盖） | ~15s |

## 用法

```bash
# WSL Ubuntu，仓库根
bash scripts/repro/verify_arm.sh
bash scripts/repro/verify_qemu_gdb.sh
bash scripts/repro/verify_ghidra.sh

# fuzz smoke 独占总线：勿与其它 CAN 测试并行
bash scripts/repro/verify_fuzz_smoke.sh         # ISO-TP 层
bash scripts/repro/verify_uds_fuzz_smoke.sh     # UDS 请求层（v1 SF + OQ3 MF）

# 网关守护进程（OQ7）同样独占总线
bash scripts/repro/verify_gateway_runtime.sh
```

## 与完整实验的关系

- **fuzz**：本目录提供**两个**最小闭环 —— ISO-TP 层
  `verify_fuzz_smoke.sh`（3 case）与 UDS 请求层
  `verify_uds_fuzz_smoke.sh`（3 case；另冻结 v1 golden 947 case 与两个 digest）。
  **完整 campaign 目前只有 UDS 层有真实入口**（`uds_fuzz.py --max-cases N`，
  vcan）；ISO-TP 层的 `isotp_fuzz.py` 在未给 `--smoke` / `--list` 时会
  **拒绝运行**（「本阶段不做大规模 campaign」，exit 2），其 smoke 集合
  上限 30 case（`build_smoke_cases()` 返回 `cases[:30]`）。
  产物写入 `labs/vehicle/can-tools/fuzz-out/`（已 gitignore），不进默认 CI。
- **Ghidra**：只验证"导入→分析→架构识别→反汇编"的最小 headless
  流水线；完整逆向项目模板/脚本库属第二轮可选增强。
- 历史审计证据脚本仍留在 `tmp/audit/`（gitignore），其中与仓库未来
  状态相关的验证已迁移为这里的版本化入口。
