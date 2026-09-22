# SecurityLab 架构说明（ARCHITECTURE）

> 面向后续维护者：说明环境分层、车联网实验协议栈、模块职责与测试/CI 拓扑。细节以源码与 `FINAL_ENVIRONMENT_AUDIT_2026-09-20.md` 为准。

## 1. 总体分层

```
┌─────────────────────────────── Windows 11 宿主（i7-12700H / 16G） ───────────────────────────────┐
│  D:\SecurityLab（Git 工作树，源真）   D:\WSL\{Ubuntu,Kali}\ext4.vhdx   D:\Docker\...\docker_data   │
│  Docker Desktop 4.90（Engine 29.7.2，WSL 集成，数据盘受保护不 prune）   Clash Verge :7890          │
└───────────────┬───────────────────────────────────┬───────────────────────────────────────────────┘
                │ WSL2（内核 6.18.33.2，.wslconfig 6G/8C/2G NAT）
      ┌─────────▼──────────┐               ┌──────────▼─────────┐            ┌───────────────────────┐
      │ Ubuntu 24.04（主）  │  binfmt_misc  │ kali-linux（辅）   │            │ docker-desktop（引擎） │
      │ systemd 255        │◄─全局共享表──►│ systemd 260        │            │ Desktop 托管，SOP 恢复 │
      │ lilike(sudo/docker)│  selfheal v2.2│ Rolling            │            └───────────────────────┘
      ├────────────────────┤               ├────────────────────┘
      │ vcan0(systemd 自启) │               │ 逆向/取证/无线工具集
      │ venv ~/.venvs/net  │
      │ dsh-web(user svc)  │── 127.0.0.1:8731（linger 开机自启）
      └────────────────────┘
```

- **源真唯一**：所有实验代码/文档/可复现配置放 `D:\SecurityLab`（WSL `/mnt/d/SecurityLab`）。系统侧安装（apt、/opt/ghidra、systemd 单元）在文档中登记，可由 `configs/` 复现。
- binfmt_misc 是**内核全局表**，各 distro 在独立 mount namespace 挂载同一张表；selfheal v2.2 以 additive 方式维护，避免跨 distro 振荡（详见审计报告第 2 节）。

## 2. 仓库布局

```
SecurityLab/
├── labs/                        # 核心实验（源真）
│   ├── vehicle/
│   │   ├── can-basic/           # CAN 基础练习
│   │   ├── can-tools/           # CAN/ISO-TP 工具链：收发/捕获/fuzz/replay
│   │   └── uds-basic/           # UDS 服务端/客户端/网关 + 测试（本任务重点）
│   ├── firmware/                # binwalk-basic、synthetic-ecu（合成固件）、samples（样本管理）
│   ├── reverse/{arm64,hello}/   # 逆向入门
│   ├── fuzzing/afl-demo/        # AFL++ 通用 fuzz 靶场
│   └── yara-basic/              # YARA 规则 + 样本 + scan.sh
├── configs/                     # 可复现环境配置
│   ├── binfmt/                  # additive 注册器 + selfheal v2.3（fail-closed）
│   ├── environment/             # python-network / python-dev requirements（钉版本）
│   ├── network/                 # proxy.env（动态探测）、ssh-proxy.sh
│   └── systemd/                 # vcan0、binfmt-selfheal、dsh-web 等单元
├── scripts/                     # healthcheck / qa_fast / qa_extended / nettools_check
│   ├── repro/                   # 关键验证可复现入口（verify_arm / verify_qemu_gdb /
│   │                            #   verify_ghidra / verify_fuzz_smoke）
│   └── tests/                   # shell 级回归（healthcheck fallback、binfmt fail-closed）
├── docs/                        # 审计报告、测试矩阵、状态看板、架构、断点文件
├── pyproject.toml conftest.py   # ruff/mypy/pytest 配置；vcan marker 自动打标
├── .github/workflows/ci.yml     # fast 自动 / extended 手动
└── （历史/用户目录：ctf data firmware notes pcaps projects reports reverse tools
      artifacts —— 非本任务创建，保持原样不动）
```

## 3. 车联网实验协议栈（uds-basic / can-tools）

```
            离线 unit（无 vcan）                         实时 e2e（独占 vcan0）
  ┌───────────────────────────┐            ┌───────────────────────────────────────┐
  │ test_ecu_unit 105         │            │  client/isotp_client.py (Tester)       │
  │ test_keygen_unit 34       │            │  client/uds_client.py                  │
  │ test_services_extra 17    │            │        │  UDS 请求（0x10/11/19/22/27/2E/31/34/35/36/37/3E）│
  │ test_gateway_unit 13      │            │        ▼  ISO-TP（Normal 11-bit）        │
  │ test_dtc_unit 30          │            │                                       │
  │ test_routine_unit 25      │            │                                       │
  │ test_transfer_unit 40     │
  │ test_upload_unit 47       │            │                                       │
  └───────────────────────────┘            │  SocketCAN vcan0  (TX 0x7E0/RX 0x7E8)  │
                                           │        ▲                                │
  ┌───────────────────────────┐            │  ecu/ecu.py 主循环（帧收发/空响应抑制）  │
  │ 协议层（纯 Python，无 socket）│          │        │  分发                           │
  │ ecu/uds_server.py         │◄───────────┤        ▼                                │
  │  - EcuState(会话/安全/DID) │  同一份业务  │  ecu/uds_server.py：service_* 处理     │
  │  - SERVICES 注册表         │   逻辑      │  ecu/keygen.py（0x27 seed/key）        │
  │ ecu/gateway.py            │            │  ecu/did_database.py（含可写 DID）      │
  │  - VirtualEcu/Diagnostic- │            └───────────────────────────────────────┘
  │    Gateway（多 ECU 路由）   │
  │ ecu/uds_types.py / did_*  │
  └───────────────────────────┘
```

### 3.1 寻址与多 ECU 拓扑

- 单 ECU：Tester→ECU CAN ID 0x7E0，ECU→Tester 0x7E8；RECV_TIMEOUT=1.5s。
- 多 ECU（`ecu/gateway.py`，`build_default_gateway()`）：

```
Tester ──物理寻址──► ECU_A 0x7E0 / 应答 0x7E8
       ──物理寻址──► ECU_B 0x7E1 / 应答 0x7E9
       ──物理寻址──► ECU_C 0x7E2 / 应答 0x7EA
       ──功能寻址──► 0x7DF（广播：三 ECU 同改状态，网关不收集/不泄漏正响应）
```

  - 每个 `VirtualEcu` 持有独立 `EcuState`（会话/安全等级/DID 覆盖互不影响，已由"只写 A 不影响 B/C"用例验证）。
  - 未知 CAN ID 返回 `[]`；空响应 `b""` 不发帧；空网关/重复 request_id 抛 `ValueError`。
  - **当前 VERIFIED**：协议层路由（13 单元）+ 三 ECU 进程物理寻址实时（4 e2e，`ecu.py` 经 `UDS_INTERFACE/UDS_RX_ID/UDS_TX_ID` 起多实例）。
  - **PARTIAL**：0x7DF 功能寻址的用户态实时网关守护进程（目标拓扑 `Tester→CAN→Gateway→ECU A/B/C`）待建。

### 3.2 UDS 状态机与服务

```
会话 Session: DEFAULT(01) ──0x10 03──► EXTENDED(03)
                     └────0x10 02────► PROGRAMMING(02)；0x11 hardReset 回默认并清 DID 覆盖
安全 Security: LOCKED(00) ──0x27 请求 seed→发 key(derive/verify)──► LEVEL1(01)
              连续失败 3 次 → lockout 10s（UDS_LOCKOUT_SECONDS 可覆盖）；seed 5s 过期
```

- 已实现：0x10 SessionControl、0x11 ECUReset、0x19 ReadDTCInformation（reportType 01/02/0A，静态 dtc_database + EcuState.dtc_records，hardReset 保留 DTC）、0x22 ReadDataByIdentifier、0x27 SecurityAccess、0x2E WriteDataByIdentifier、0x31 RoutineControl（Start/Stop/Results，0x2001/0x2002 synthetic routines，hardReset 清 routine 状态、session 切换保留）、0x34/0x36/0x37 Download Transfer（synthetic in-memory 目标、DFI=00/ALFID=23、BSC 块序+幂等重传+回绕、size accounting、0x37 完整性检查后清理状态、hardReset 中止下载）、0x35 RequestUpload（256 字节循环源、固定块契约+上一块重建、duplicate 幂等重发、32130 上限、0x36/0x37 方向分流且 download 路径不变）、0x3E TesterPresent。
- 响应：正响应 `[sid+0x40, ...]`；否定响应 `7F sid nrc`。NRC：10/11/12/13/22/24/31/33/35/36/37/73 + 7F（默认会话不支持该服务）。
- 0x2E 门控顺序：长度→未知 DID(31)→默认会话(7F)→未解锁(33)→只读 DID(22)→长度越界(13)→成功 6E；写 `state.did_overrides`，0x22 读 override 优先，0x11 清空。
- 0x3E：`3E 00→7E 00`；`3E 80→抑制不发帧`；非法子功能 12；长度错 13。
- 未实现（路线图，禁空壳）：0x38 / 0x39、真实 Flash 与签名校验、Permission/session framework、0x19 其余 reportType、0x31 的 option/status record，每个须 request/positive/NRC/state/unit/e2e 齐备。

### 3.3 Fuzz / Replay / Capture 数据流

```
isotp_corpus.py ──种子──► isotp_mutators.py ──► isotp_fuzz.py(--smoke/--dimensions/--categories)
                                              │  经 vcan0 打向 ECU，逐例记录分类与健康
                                              ▼
                         fuzz-out/camp-*/  testcase-*.json + smoke-summary.json（生成物不入 Git）
                                              │
replay.py <path|--all|--repeat N> ◄────────────┘  （parsers.py 只采 testcase，[ignore] summary/metadata）
can_capture.py / can_capture_blf.py / capture/capture_lab.sh：
   vcan0 ──► CAN log(candump -L→canplayer) / BLF(python-can BLFWriter/Reader) / PCAP(tshark)
   三格式保存→回放 diff 一致（CAPTURE_LAB=PASS）。PCAP 定位 Wireshark 分析，回放用 CAN log/BLF。
```

- fuzz 分类：NORMAL / UDS（诊断正/负响应）/ TE（transport/transport-error）/ DISCARD（被丢弃）。
- 每例强制健康检查：ECU process_alive、recovery、new_errno、kernel、cleanup；`--seed 12345` 确定性。
- kernel oracle 为**三态**（AVAILABLE / UNAVAILABLE / ERROR，记录于
  `result.kernel_oracle`）：只有 AVAILABLE 时 `kernel_events` 才有意义；
  oracle 不可用时 `kernel_events`/`kernel_delta_lines` 记为 `None`（不可判定），
  **绝不伪装成 0 kernel event**。smoke summary 汇总三态计数。

## 4. 逆向 / 固件 / 通用 Fuzz / IOC

```
源码(C) ──aarch64/armhf-gcc -static -g──► ELF ──┬─ binfmt/qemu(-g) 透明/挂起执行
                                                ├─ gdb-multiarch target remote（断点/单步/继续）
                                                ├─ Ghidra 12 headless（analyzeHeadless + postScript）
                                                ├─ radare2/rabin2、objdump/readelf/nm/strings/file
                                                └─ strace/ltrace/qemu -strace（syscall/库调用）
固件分析链（labs/firmware/synthetic-ecu：build→analyze→firmware_diff→fs_formats，合成、可复现）：
  SquashFS   build + identify + extract + analyze（完整链）
  JFFS2      build + identify（当前环境缺少完整 extraction chain，见 STATUS 路线图）
  UBIFS/UBI  build + identify（当前环境缺少完整 extraction chain，见 STATUS 路线图）
靶场 vuln.c ──afl-gcc 插桩──► afl-fuzz ──► crash（sig6/134）──► 样本重放复现（labs/fuzzing/afl-demo）
样本 ──yara（labs/yara-basic/rules）──► TP/TN 判定，并扫描合成固件
```

## 5. Python 依赖与质量门禁

- 系统 Python 保持干净；研究依赖在 venv `~/.venvs/network`（Python 3.12.3）。
- `configs/environment/python-network-requirements.txt` 钉运行时 8 包；
  `python-dev-requirements.txt` 钉 pytest/ruff/mypy/pip-tools。
- **依赖可重复性（P2，2026-09-21 已收口）**：
  `python-network-requirements.lock` 为 pip-compile（pip-tools 7.6.1）
  从 direct pins 解析出的 **transitive lock**（direct → transitive →
  固定版本）。已在全新 venv 只装 lock 验证可导入
  （LOCK_FRESH_INSTALL_OK，can 4.6.1 / isotp 2.0.7）。lock 再生成命令
  见 `python-dev-requirements.txt` 注释；生成需经
  `configs/network/proxy.env` 代理 + TUNA 镜像。
- `pyproject.toml`：ruff 选 E/F/I/UP/B（忽略 E501）、mypy py312、pytest 注册 `vcan` marker；`conftest.py` 按文件名含 `_vcan` 自动打标。
- 门禁：

```
qa_fast.sh（离线，独立进程）          qa_extended.sh（独占 vcan，串行）
 ├─ ruff check uds-basic              ├─ 确保 vcan0（root/免密 sudo 自建，否则报错退出）
 ├─ mypy ecu 包                       ├─ uds run_tests all
 ├─ uds run_tests unit (312)          ├─ can-tools run_tests all
 └─ can-tools run_tests offline(183)  ├─ capture_lab（三格式）
                                      └─ yara scan；[--fuzz] 追加 CAN fuzz smoke
healthcheck.sh [--extended]：环境/工具/服务/链路总检（PASS/WARN/FAIL，FAIL→exit 1）
CI .github/workflows/ci.yml：fast 自动（push/PR）、extended 仅 workflow_dispatch；fuzz 不进 CI
scripts/repro/：ARM/QEMU-GDB/Ghidra/fuzz smoke 最小可复现验证（换机可重跑）
```

### 5.1 三层测试机制的职责边界（不要把所有实验塞进 pytest）

| 机制 | 定位 | 典型场景 |
|------|------|----------|
| `pytest`（根 pyproject） | 开发期局部测试、快速调试 | 单文件单模块；`-m "not vcan"` 快速筛选 |
| `labs/*/tests/run_tests.sh` | **实验级验证**（权威离线门） | 每个测试文件**独立解释器进程**；vcan / 真实实验流程（ECU 子进程、ISO-TP 链路） |
| `scripts/qa_fast.sh` / `qa_extended.sh` | **环境门禁**（工程级质量检查） | ruff+mypy+全量离线；extended 串行独占 vcan 的真实闭环 |

**为什么不全部塞进 pytest 单会话收集**：

1. 两个"模块导入纯净度"用例断言 `sys.modules` 不含 `can`/`isotp`——单会话
   pytest 收集会跨模块污染 `sys.modules`，这类用例**天然要求独立解释器进程**；
2. vcan e2e 需要**独占总线**且每例起/停 ECU 子进程，必须串行、有清理保证，
   适合 run_tests.sh / qa_extended 的编排而不是 pytest 并行收集；
3. 测试入口的 Python 解释器（venv）选择、总线预检（vcan0 存在且 UP）、
   环境提示与退出码语义由 run_tests.sh 统一表达，pytest 只管用例本身。

因此：pytest 可跑（如 `pytest -m "not vcan"`），但**权威离线门**是
`qa_fast.sh` / `run_tests.sh`；vcan 类验证的权威入口是
`qa_extended.sh` / `run_tests.sh all`。

## 6. 服务与自启

| 单元 | 级别 | 作用 | 自启 |
|------|------|------|------|
| vcan0.service | system | 开机建立 vcan0（MTU 2060）+ 加载 can/can_raw/vcan/can-isotp | enabled |
| binfmt-selfheal.path/.timer(+service/quick) | system | binfmt additive 自愈（path 触发 + 10s 兜底；v2.3 fail-closed，helper 缺失不回落 systemd-binfmt） | enabled，双端 |
| ssh-agent.service | user | SSH_AUTH_SOCK（密钥仍需用户 ssh-add 解 passphrase） | active |
| dsh-web.service | user | DSH Web GUI 于 127.0.0.1:8731（Restart=on-failure） | enabled + linger |

## 7. 设计约束（维护时遵守）

- CAN/UDS 一切实验只对 vcan0 与虚拟 ECU；不接真实车辆/ECU/适配器。
- 生成物（fuzz-out、固件镜像、提取目录、编译产物、tmp/audit）不入 Git；样本走 `labs/firmware/samples` MANIFEST 管理。
- 不 prune Docker、不删数据卷/vhdx/distro；系统级改动可逆并在 `configs/` 留痕。
- 新增 UDS 服务必须六件套齐备（request/positive/NRC/state/unit/e2e），拒绝无测试依据的空壳。
- vcan 测试独占总线、严禁并发；长任务用持久进程，勿据 run_in_background 的 -1 回显前台重跑。
