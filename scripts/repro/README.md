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

## 用法

```bash
# WSL Ubuntu，仓库根
bash scripts/repro/verify_arm.sh
bash scripts/repro/verify_qemu_gdb.sh
bash scripts/repro/verify_ghidra.sh

# fuzz smoke 独占总线：勿与其它 CAN 测试并行
bash scripts/repro/verify_fuzz_smoke.sh
```

## 与完整实验的关系

- **fuzz**：本目录只提供 3-case smoke（harness/schema/分类/oracle/
  replay 的最小闭环）。完整 campaign（smoke30 / dim100 / dim500）
  仍是实验行为，产物在 `labs/vehicle/can-tools/fuzz-out/`（已
  gitignore），不进默认 CI。
- **Ghidra**：只验证"导入→分析→架构识别→反汇编"的最小 headless
  流水线；完整逆向项目模板/脚本库属第二轮可选增强。
- 历史审计证据脚本仍留在 `tmp/audit/`（gitignore），其中与仓库未来
  状态相关的验证已迁移为这里的版本化入口。
