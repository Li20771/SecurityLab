# ENGINEERING CLOSE REPORT — SecurityLab 第一轮工程收口

- 日期：2026-09-21
- 范围：修复审计确认的 P1/P2 工程问题、补足可复现入口、同步文档；
  **不新增任何功能**、不改 main、不改第一轮行为语义。
- 权威状态：本文档 + `SECURITY_LAB_STATUS.md` + `TEST_MATRIX.md`；
  审计窗口历史见 `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md`（已标注为历史快照）。

---

## 1. Branch

```text
branch:       chore/engineering-close-20260921
base:         audit/environment-20260921 (snapshot commit 14ddb75bcfad5105d8b91455ed4d4cfe2fae8c6a)
main:         e215b1d（本分支未触碰）
final commit: fe1ad76fccba745bfd7fa6f3b24fa6e4aeaa5881
              （fix(repro): align Ghidra verification with round-1 AArch64
               target and correct report evidence —— 快照数值冻结点；
               随后的 docs-only 终稿提交见 git log 首条）
```

## 2. 修改文件

### P1（本阶段必须修的工程问题）

| 文件 | 变更 |
|------|------|
| `labs/vehicle/can-tools/fuzz/isotp_fuzz.py` | dmesg_snapshot 三态、`kernel_oracle_result`、result.kernel_oracle、summary 三态计数 |
| `labs/vehicle/can-tools/fuzz/case_schema.py` | KERNEL_ORACLE_{AVAILABLE,UNAVAILABLE,ERROR} + `combine_oracle_status()` |
| `labs/vehicle/can-tools/fuzz/replay.py` | 输出行显示 oracle 状态 |
| `configs/binfmt/binfmt-selfheal.sh` | v2.3 fail-closed（移除 systemd-binfmt 回退）+ 测试接缝 |
| `scripts/healthcheck.sh` | ARM64 qemu fallback 退出码显式捕获（修复 `&&` 短路） |

### P2

| 文件 | 变更 |
|------|------|
| `configs/environment/python-network-requirements.lock` | **新增**：pip-compile transitive lock（direct → transitive → 固定版本） |
| `configs/environment/python-dev-requirements.txt` | 追加 pip-tools==7.6.1（lock 再生成工具） |

### Docs

| 文件 | 变更 |
|------|------|
| `README.md` | UDS 服务清单（0x10/11/22/27/2E/3E + registry）、固件能力分文件系统表述、测试规模实测值、目录结构 |
| `labs/vehicle/uds-basic/README.md` | SID 表、0x2E 门控链、0x3E 语义、NRC 0x7F、错误矩阵、测试规模表（8 文件 259 例）、SERVICES 表、收口基线、路线图去重 |
| `docs/SECURITY_LAB_STATUS.md` | 去除历史性建议（gitignore/未提交 commit）、repro 入口、fail-closed、路线图 |
| `docs/TEST_MATRIX.md` | 来源语义（Local verified / Environment audit reported / GitHub CI verified）、oracle 三态、repro 入口、CI 如实表述 |
| `docs/ARCHITECTURE.md` | 固件链精确化（SquashFS 全链；JFFS2/UBIFS build+identify）、§5.1 三层测试职责、依赖 lock 状态 |
| `docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md` | 顶部标注"历史快照" |
| `docs/AUTONOMOUS_BUILD_STATE.md` | 追加 2026-09-21 工程收口断点 |
| `docs/ENGINEERING_CLOSE_2026-09-21.md` | **新增**：本报告 |

### Tests

| 文件 | 变更 |
|------|------|
| `labs/vehicle/can-tools/tests/test_fuzz_offline.py` | +10 例 kernel oracle 三态（含"不可用 ≠ 0 events"不变量） |
| `configs/binfmt/tests/selfheal_failclosed_test.sh` | **新增**：fail-closed A/B/C 三态 + 源码护栏（11 断言） |
| `scripts/tests/healthcheck_qemu_fallback_test.sh` | **新增**：fallback 语义回归（3 场景，PATH stub） |

### Repro

| 文件 | 变更 |
|------|------|
| `scripts/repro/verify_arm.sh` | **新增**：AArch64/armhf 编译 + 执行（binfmt 优先、qemu 回退） |
| `scripts/repro/verify_qemu_gdb.sh` | **新增**：qemu -g ↔ gdb-multiarch 断点闭环 |
| `scripts/repro/verify_ghidra.sh` | **新增**：analyzeHeadless 导入→分析→架构→反汇编 |
| `scripts/repro/ghidra_scripts/DumpDisasm.java` | **新增**：postScript（自 `tmp/audit/` 版本化） |
| `scripts/repro/verify_fuzz_smoke.sh` | **新增**：3-case fuzz smoke（harness/schema/分类/oracle/replay） |
| `scripts/repro/README.md` | **新增**：入口说明与退出码语义 |

## 3. 每个问题

### P1-A fuzz kernel oracle 伪装 "0 kernel event"

- **问题**：`dmesg_snapshot()` 失败时返回 `[]`，下游把空列表当作"检查成功且无内核事件"，无法区分"oracle 正常无命中"与"oracle 根本不可用"。
- **原因**：单一返回值（行列表）无法承载三态语义；异常被静默吞掉。
- **修改**：
  - `dmesg_snapshot() -> (status, lines)`，status ∈ AVAILABLE（成功执行，含 0 行）/ UNAVAILABLE（二进制缺失、非零退出）/ ERROR（超时、意外 OSError）；
  - 每例结果新增 `result.kernel_oracle`（before+after 合并，ERROR 优先于 UNAVAILABLE）；
  - oracle 非 AVAILABLE 时 `kernel_events` / `kernel_delta_lines` 记 `None`（不可判定），**绝不伪报 0**；
  - smoke summary 与逐例输出显式打印三态计数；oracle 不可用时明确提示"KERNEL_ANOMALY 分类降级为不可判定"；
  - 分类优先级完全不变；`schema_version` 保持 1（result 字段纯增量，不参与 replay identity）。
- **验证**：
  - `test_fuzz_offline.py` 99 例 PASS（新增 10 例：dmesg 正常/二进制缺失/非零退出/超时/OSError、状态合并、不可用→(None, False) 不变量、AVAILABLE 兼容行为）；
  - `scripts/repro/verify_fuzz_smoke.sh` 8/8 PASS，真实环境 oracle AVAILABLE=3 如实上报；
  - `qa_fast` / `qa_extended` 全 PASS（既有分类结果未变）。

### P1-B binfmt selfheal 回退 systemd-binfmt

- **问题**：additive helper 缺失时回落 `/usr/lib/systemd/systemd-binfmt`，它先 flush 内核全局注册表（删 WSLInterop 与其它 distro 条目），重新触发 v2.2 已消除的跨 distro 振荡——违背 additive-only 设计原则。
- **原因**：v2.1 时代"boot 期可接受 flush"的遗留 fallback 未被移除。
- **修改**（v2.3，fail-closed）：
  - helper 存在 → 执行并向上返回其退出码；
  - helper 缺失 → `logger -p user.err` 明确报错、返回非零、**绝不调用 systemd-binfmt**；
  - helper 执行失败 → 错误记日志（每轮一次，容忍 boot 竞态瞬态失败），最终判定返回非零；
  - 新增测试接缝 `BINFMT_REGISTRY / BINFMT_LOCK / BINFMT_HELPER / BINFMT_D_DIRS`（默认值不变，仅测试用）。
- **验证**：`configs/binfmt/tests/selfheal_failclosed_test.sh` **11/11 PASS**——情况 A（helper 正常，quick 退出 0 且条目被注册）、情况 B（helper 缺失，退出 1、日志含 missing+fail-closed、register 未被写入）、情况 C（helper 失败，退出 1、错误向上）、源码级护栏（无 systemd-binfmt 执行调用）。
- **binfmt 状态必须分开表述**（未做 sudo 部署，不得写成"fully fixed"）：

```text
Repository source:    v2.3 fail-closed（configs/binfmt/binfmt-selfheal.sh）
Current deployed system: not upgraded（/usr/local/sbin/binfmt-selfheal.sh
                      仍是审计期部署版本；升级需 sudo 授权）→ BLOCKED / NOT DEPLOYED
```

### P1-E healthcheck QEMU fallback `&&` 短路

- **问题**：`elif command -v qemu-aarch64 && qemu-aarch64 "$t" && [ "$?" = "42" ]` ——qemu-user **透传 guest 退出码**（guest return 42 → qemu 退出 42，非零），`&&` 链短路，条件恒为假：binfmt 透明执行不可用时 fallback 永远不触发，环境明明可用却报 FAIL。
- **原因**：把"命令退出码"与"guest 退出码"混在同一 `&&` 链里判断。
- **修改**：透明执行先跑并显式保存 `RC`；`RC != 42` 时独立执行 qemu 并保存 `QRC` 再比较；FAIL 信息同时报出两个 rc。fallback 语义不变（透明优先、qemu 次之、都期望 42）。
- **验证**：
  - `scripts/tests/healthcheck_qemu_fallback_test.sh` **3/3 PASS**（qemu stub 退出 42 → fallback 命中 PASS；qemu stub 退出 0 → FAIL 且报双 rc；静态断言透明执行优先）；
  - 真实环境 `healthcheck.sh` **PASS 75 / WARN 2 / FAIL 0**（binfmt 主路径 `transparent exec (exit 42)` PASS，主路径不受影响）。

### P1-C 文档与源码不一致

逐条对齐（详见 §2 Docs 表格与两个 commit）：

- 根 README / UDS README：`0x2E / 0x3E / service registry` 从"未来工作"改为"已实现"，未实现清单只保留 0x14/0x19/0x28/0x31/0x85；
- STATUS：删除"建议加入 .gitignore"（已生效）与"44 项未提交"等历史性条目；
- ARCHITECTURE：固件链改为按文件系统精确表述（SquashFS build+identify+extract+analyze；JFFS2 build+identify；UBIFS/UBI build+identify，extraction chain 缺失）；
- TEST_MATRIX：来源语义三分类（Local verified / Environment audit reported / GitHub CI verified），CI 未实跑如实标注。

### P1-D 关键验证不可复现

- **问题**：ARM/QEMU-GDB/Ghidra 验证证据仅在 `tmp/audit/`（gitignore），换机无法只靠 repo 重现。
- **修改**：新增 `scripts/repro/`（§5），统一退出码语义（0 过 / 1 验证失败 / 2 环境缺失），全部 mktemp 产物即用即删。
- **Ghidra repro 与第一轮目标对齐（证据修正）**：脚本自身生成
  AArch64 静态 ELF（`aarch64-linux-gnu-gcc -static -g`），断言
  `Architecture = AARCH64` 与 `ERROR_COUNT = 0`，postScript 反汇编
  main 12 条 AArch64 指令——即"**AArch64 ELF → headless analyze →
  AARCH64 language → ERROR_COUNT=0 → AArch64 反汇编**"完整闭环，
  不再使用 x86 sample。
- **验证**：见 §5 实测结果（2026-09-21 证据修正后全量重跑）。

### P1-F 测试层级混乱

- **修改**：`docs/ARCHITECTURE.md` §5.1 明确三层职责——pytest（开发期局部调试）/ `run_tests.sh`（实验级验证，独立解释器进程 + vcan 流程）/ `qa_*`（环境门禁）；说明为什么不能全塞进 pytest（模块纯净度用例要求独立进程、vcan 独占串行、解释器选择与总线预检由脚本层承担）。

### P1-G 依赖 transitive lock（P2）

- **修改**：`python-network-requirements.lock`（pip-tools 7.6.1 生成；direct → transitive → 固定版本）；pip-tools 入 dev requirements；生成方式写入注释。
- **验证**：全新 venv 只装 lock → `LOCK_FRESH_INSTALL_OK`（can 4.6.1 / isotp 2.0.7 等全部可导入）。
- **注意**：pip-tools 7.6.1 自动 header 误记 `--no-index`（无此配置亦未传参），已替换为准确说明，包列表未改动。

## 4. 测试结果（2026-09-21 实测）

| 层级 | 项目 | 结果 | 状态 |
|------|------|------|------|
| L1 静态 | `git diff --check` | 无输出 | **PASS** |
| L1 静态 | ruff（uds-basic） | All checks passed | **PASS** |
| L1 静态 | mypy（ecu 包） | 0 issues | **PASS** |
| L2 离线 | uds unit（104+34+17+13=168） | `run_tests.sh unit` 4/0/0 | **PASS** |
| L2 离线 | can-tools offline（34+50+99=183） | `run_tests.sh offline` 3/0/0 | **PASS** |
| L2 离线 | `qa_fast.sh` | QA FAST RESULT: PASS | **PASS** |
| L3 vcan | uds all（unit+e2e 91，多 ECU、malformed 前置链路） | PASS 9 / WARN 0 / FAIL 0 | **PASS** |
| L3 vcan | can-tools all（含 malformed 47，~150s） | PASS 5 / WARN 0 / FAIL 0 | **PASS** |
| L3 vcan | capture 三格式 | CAPTURE_LAB=PASS | **PASS** |
| L3 vcan | replay（3 case --all） | issue(s)=0，rc=0 | **PASS** |
| L3 vcan | YARA TP/TN | YARA_RESULT=PASS | **PASS** |
| L3 vcan | `qa_extended.sh` | QA EXTENDED RESULT: PASS | **PASS** |
| L4 环境 | `healthcheck.sh` | PASS 75 / WARN 2 / FAIL 0 | **PASS**（见 WARN） |
| L4 环境 | `healthcheck.sh --extended` | PASS 78 / WARN 2 / FAIL 0 | **PASS**（见 WARN） |
| L4 环境 | `verify_arm.sh` | 6/6 | **PASS** |
| L4 环境 | `verify_qemu_gdb.sh` | 4/4 | **PASS** |
| L4 环境 | `verify_ghidra.sh` | 8/8（AArch64 样本：Architecture=AARCH64、ERROR_COUNT=0、main 反汇编 12 条） | **PASS** |
| L4 环境 | `verify_fuzz_smoke.sh` | 8/8 | **PASS** |
| L4 环境 | binfmt fail-closed 回归 | 11/11 | **PASS** |
| L4 环境 | healthcheck fallback 回归 | 3/3 | **PASS** |

### WARN（非失败，如实报告）

1. healthcheck `Git working tree has uncommitted changes`：来自预存在的
   `.p1x-probe/n01-acceptance2-OUTPUT.txt` 行尾 artifact（WSL 侧 git 可见
   `-INTEROP_OK` / `+INTEROP_OK` 同文行尾差）。**非本分支改动**，未擅自
   restore；处置建议（用户确认后）：`git checkout -- .p1x-probe/n01-acceptance2-OUTPUT.txt`。
2. healthcheck `docker engine not reachable`：Docker Desktop 未运行
   （审计 SOP 已覆盖：`wsl --terminate docker-desktop` 自动恢复），非本分支引入。

### BLOCKED（不要读成 PASS）

| 项 | 阻塞原因 |
|----|----------|
| GitHub CI 真实 runner 运行 | 无 push 授权，workflow 未触发；本地等价环境 qa_fast/qa_extended 全绿 |
| selfheal v2.3 系统侧部署（`/usr/local/sbin/` 双端） | 需 sudo 授权；repo 为源真，fail-closed 回归已在假注册表完成 |
| 全 VM 冷启动级验证（`wsl --shutdown`） | 宿主安全策略禁止；本轮改动为脚本级逻辑，不改变冷启动路径（v2.2→v2.3 仅移除 fallback 分支） |
| 完整 fuzz campaign 重跑（smoke30/dim100/dim500） | 属实验行为，不进收口门禁；最小 smoke（3 case）已过 |

## 5. Reproducibility（scripts/repro/*）

| 脚本 | 执行 | 结果（本机实测） |
|------|------|------------------|
| `verify_arm.sh` | `bash scripts/repro/verify_arm.sh` | 6/6：AArch64 编译+执行（binfmt/qemu 回退）、armhf 编译、qemu-arm 执行、readelf 架构 |
| `verify_qemu_gdb.sh` | `bash scripts/repro/verify_qemu_gdb.sh`（端口 `REPRO_GDB_PORT` 可覆盖） | 4/4：qemu -g 挂起→gdb remote→main 断点→continue→rc0 |
| `verify_ghidra.sh` | `bash scripts/repro/verify_ghidra.sh`（`GHIDRA_BIN` 可覆盖） | 8/8：AArch64 ELF（脚本自生成）→ analyzeHeadless rc0 → Architecture=AARCH64 → ERROR_COUNT=0 → main 反汇编 12 条 AArch64 指令 |
| `verify_fuzz_smoke.sh` | `bash scripts/repro/verify_fuzz_smoke.sh`（**独占 vcan**；`PYTHON` 可覆盖） | 8/8：3 case harness→schema/分类→oracle 三态→replay issue(s)=0 |

统一约定：依赖缺失退出 2（环境不可用，非验证失败）；验证失败退出 1；产物在 mktemp 目录即用即删。完整 fuzz campaign 继续属于实验，不进默认 CI。

## 6. 未完成事项（明确保留，本分支未做）

- **第二轮功能建设（全部不做）**：UDS 0x19 / 0x31 / 0x34 / 0x35 / 0x36 / 0x37；
  实时三 ECU Gateway daemon（0x7DF 用户态守护进程）；Docker Security Lab；
  完整 JFFS2 / UBIFS extraction chain；大型新 fuzz campaign；Obsidian 知识系统；
  新的真实 CAN 接口（需明确授权范围）。
- **仍阻塞的 P2**：GitHub CI runner 实测（待 push 授权）；selfheal v2.3
  双端系统部署（待 sudo 授权）。
- **当前环境限制**：`wsl --shutdown` 被宿主策略禁止（冷启动级验证受限）；
  Docker Desktop 当前未运行（SOP 可恢复）。
- **用户待办**：`ssh-add`（私钥 passphrase）；0 字节怪文件
  `` ; else echo MISS `` 处置；`docker_data.vhdx` 压缩授权；本分支 push/PR 授权。

## 7. Git

```bash
$ git status
On branch chore/engineering-close-20260921
nothing to commit, working tree clean

$ git rev-parse HEAD
fe1ad76fccba745bfd7fa6f3b24fa6e4aeaa5881

$ git log --oneline audit/environment-20260921..HEAD   # 10 个 commit
fe1ad76 fix(repro): align Ghidra verification with round-1 AArch64 target and correct report evidence
07be67a docs: correct close report final diffstat and commit count
a772423 docs: finalize engineering close report with git evidence
45c72ca docs: add engineering close report
7d6db1c docs: synchronize environment and UDS status with source reality
258bf07 chore(deps): add transitive lock for network requirements
40fef72 chore(repro): add reproducible verification entrypoints
d738822 fix(healthcheck): correct qemu fallback status handling
549a212 fix(binfmt): remove unsafe selfheal fallback
4f5fb62 fix(fuzz): make kernel oracle fail-closed

$ git diff --check
（无输出，通过）

$ git diff audit/environment-20260921...HEAD --stat | tail -1
 24 files changed, 2138 insertions(+), 198 deletions(-)

$ git diff main...HEAD --stat | tail -1
 112 files changed, 21993 insertions(+), 651 deletions(-)
（含自 main 基线以来整个审计分支的累积改动 + 本分支 10 个 commit）
```

- 提交纪律：10 个 commit，每个单一工程目的——fix(fuzz) / fix(binfmt) /
  fix(healthcheck) / chore(repro) / chore(deps) / docs(sync) /
  docs(close-report) / docs(finalize-report) / docs(correct-report) /
  fix(repro-ghidra-evidence)。
- main 未改动；`audit/environment-20260921` 快照未改动（以其为 base 新建分支）。
- 本分支**不合并 main**、零 push，等待用户授权。

> 本报告所有 Git 数字由命令实时生成，不手工维护：
> `git rev-parse HEAD` / `git log audit/environment-20260921..HEAD` /
> `git diff --check` / `git status` / `git diff ... --stat`。

---

## Final Evidence Snapshot（2026-09-21 证据修正后全量重跑）

> 快照数值全部由 git 命令生成，冻结点为 fe1ad76（fix(repro) evidence
> correction）；本快照随其后一条 **docs-only 终稿提交**落盘（即分支
> HEAD，见 `git log --oneline audit/environment-20260921..HEAD` 首条）。
> 终稿提交只含本小节与 §7 数值终稿，不改变任何被测对象；提交后以
> `git rev-parse HEAD` / `git log` 的输出为唯一真值（快照不自我引用）。

```text
branch:        chore/engineering-close-20260921
base:          audit/environment-20260921 (14ddb75bcfad5105d8b91455ed4d4cfe2fae8c6a)
HEAD:          fe1ad76fccba745bfd7fa6f3b24fa6e4aeaa5881（快照数值冻结点；
               终稿提交 = git log 首条 = 交付时的 git rev-parse HEAD）
working_tree:  clean（Windows 侧 git status --short 为空；
               WSL 侧 .p1x-probe/n01-acceptance2-OUTPUT.txt 行尾 artifact 见 §4 WARN）
commit_count:  10（git log --oneline audit/environment-20260921..HEAD，
               不含快照自身的 docs-only 终稿提交——该提交使计数 +1）

git_diff_check:       PASS（无输出）
repro_arm:            6/6 PASS（AArch64 compile+exec、armhf compile、qemu-arm exec）
repro_qemu_gdb:       4/4 PASS（qemu -g → gdb remote → breakpoint → continue → exit 0）
repro_ghidra:         8/8 PASS（AArch64 ELF → AARCH64 language → ERROR_COUNT=0 →
                      main 反汇编 12 条 AArch64 指令）
repro_fuzz_smoke:     8/8 PASS（harness/schema/classification/oracle/replay；
                      kernel oracle AVAILABLE=3）
binfmt_regression:    11/11 PASS（repo source v2.3 fail-closed；
                      系统部署 NOT DEPLOYED —— BLOCKED，需 sudo）
healthcheck_fallback: 3/3 PASS

GitHub CI:            NOT RUN（workflow 未在真实 runner 触发，无 push 授权）
```

---

### 验收对照

- [x] 审计问题 P1 全部修复（oracle 三态 / binfmt fail-closed / healthcheck fallback）
- [x] P2 能收口的收口（依赖 transitive lock）
- [x] 文档与源码一致（README×2 + 5 个 docs + 断点文件）
- [x] 关键验证可以重新执行（scripts/repro/，本机全过）
- [x] 测试结果可信（来源语义三分类，BLOCKED 不写 PASS）
- [x] 不新增 UDS 服务 / Gateway 功能 / Docker Lab / Obsidian / 新真实 CAN
- [x] 不改 main、不提交生成物、不修改测试预期掩盖问题
- [x] 形成干净的第二轮开发基线（分支可 review，commit 可逐条回滚）
