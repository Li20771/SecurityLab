# labs/vehicle/ota —— OTA 安全测试实验台（知识库 P16 落点）

**定位**：在现有 UDS 虚拟 ECU（uds-basic 的 0x34/0x36/0x37）之上，补**应用层 OTA 安全测试**：
固件签名校验、版本回滚/降级防护、越权升级拒绝、分块传输完整性。

**红线**：只用虚拟 ECU / vcan0 / 合成镜像与合成 manifest；不接触真实固件、真实 ECU、真实车辆。

---

## 1. 架构与校验顺序

```
OTA 客户端（测试）
  │  10 02 → 27 01/02（编程会话 + 解锁）
  ▼
0x34 RequestDownload ──OTA 门控：default session → 0x7F；未解锁 → 0x33
  ▼
0x36 TransferData ─────委托 uds-basic：错序 0x73 / 幂等 0x76 / 超长 0x13 / 越界 0x13 / 无 active 0x24
  ▼
0x37 RequestTransferExit ──传输完整时：先验签（SM2 over manifest）→ 再判版本
  │      签名失败 / 非单调 / 低于下限 → 0x72 并中止 transfer（fail-closed）
  ▼
  0x77 → current_version 更新
```

**校验顺序即安全属性**（对齐 P16 F-02 教训）：**先验签、后判版本**。
攻击者密钥签名的 `version=9.9.9` 包必须死于 `signature_invalid`，不能先被版本判定"放行"。
单测 `test_attacker_signed_package_rejected` 对此做了显式断言。

## 2. 依赖

```text
# unit（协议层，Windows/macOS/Linux 纯 Python）
gmssl==3.2.2      # labs/crypto 的 gmcrypto 依赖（SM2 签名/验签）
pytest==9.0.3

# vcan0 e2e（WSL/Linux，vcan0 已 UP）
isotp==2.0.7      # 本机 WSL venv 实测版本
udsoncan==1.25.2  # udsoncan 客户端路径
```

> 本机实测：WSL venv `/home/lilike/.venvs/network/bin/python`（3.12.3）含 isotp/udsoncan/pytest，
> 但**缺 gmssl**；`scripts/run_vcan_e2e.sh` 会在缺 gmssl 时通过 `PYTHONPATH` 复用
> Windows 侧纯 Python gmssl（未在 venv 安装任何包）。

## 3. 目录结构

```text
labs/vehicle/ota/
├── README.md            # 本文件
├── matrix.md            # 测试矩阵（PASS / NOT_RUN 三态 + 环境与修复记录）
├── ota_image.py         # 合成 OTA 包：manifest + SM2 签名/验签 + 版本解析
├── ota_ecu.py           # OTA 安全层：包装 uds-basic 的 0x34/0x36/0x37
├── ota_ecu_boot.py      # vcan0 启动脚本（复用 uds-basic transport）
├── demo_ota.py          # 可复现演示（协议层完整流程，无需 CAN）
├── scripts/
│   ├── run_vcan_e2e.sh      # WSL 运行 vcan0 e2e 并落证据
│   ├── diag_vcan.sh         # 诊断：ECU 启动 + isotp 探测
│   ├── diag_uds_baseline.sh # 诊断：uds-basic vcan 基线
│   └── diag_compare*.sh     # 诊断：uds-basic vs OTA ECU 对比
├── tests/
│   ├── conftest.py      # sys.path：ota / uds-basic/ecu / labs/crypto
│   ├── test_ota_unit.py # 19 条协议层测试（Windows 实跑）
│   └── test_ota_vcan.py # 7 条 vcan0 e2e（WSL 实跑；缺 isotp/vcan0 时整模块 skip）
└── report/
    ├── pytest_output.txt          # 协议层实测输出
    ├── demo_output.txt            # 演示脚本实测输出
    ├── pytest_vcan_output.txt     # vcan0 e2e 实测输出（WSL）
    └── uds_basic_vcan_check.txt   # uds-basic 基线对照（18 tests OK）
```

## 4. 用法

```powershell
cd labs/vehicle/ota

# 1) 协议层测试（本机，无需 CAN）
python -m pytest tests/test_ota_unit.py -v --tb=short

# 2) 可复现演示（完整 OTA 流程：解锁 → 签名包 → 验证；输出追加到 report/demo_output.txt）
python demo_ota.py

# 3) 全量（vcan e2e 在无 isotp/vcan0 的环境整模块 skip 并打印原因）
python -m pytest tests/ -v --tb=short
```

**vcan0 e2e（WSL/Linux）**：

```bash
# 前置：vcan0 已 UP；venv 含 isotp/udsoncan/pytest（gmssl 可 import，或用脚本的回退）
bash scripts/run_vcan_e2e.sh          # 或指定解释器：PYTHON=/path/to/python bash scripts/run_vcan_e2e.sh
```

## 5. 实测结果（2026-11-07）

- **协议层：19 passed**（Windows Python 3.14.4 / pytest 9.0.3 / gmssl 3.2.2）
  - 签名：合法包 `77` + 版本更新；篡改 manifest / 篡改签名 / 攻击者密钥 → `7F 37 72`
  - 降级：合法旧包 → `7F 37 72`；等版本 → 拒绝；低于回滚下限 → 拒绝
  - 越权：default session → `7F 34 7F`；未解锁 → `7F 34 33`
  - 分块：丢块/乱序 → `7F 36 73`；超长/越界 → `7F 36 13`；重复块幂等；未完成退出 `7F 37 22`；无会话 `7F 36 24`
- **演示脚本：`===== result: PASS =====`**（输出见 `report/demo_output.txt`）
  - 合法签名包 v2.4.0 → `34 -> 74 20 00 80`、`36 01/02 -> 76 01/02`、`37 -> 77`，版本 2.3.0 → 2.4.0
  - 攻击者密钥签名 v9.9.9 → `37 -> 7F 37 72`，verdict=`signature_invalid`（先验签）
  - 签名合法旧版本 v2.3.0 → `37 -> 7F 37 72`，verdict=`version_not_monotonic`（防降级）
- **vcan0 e2e：7 passed in 63.05s**（WSL Ubuntu 22.04 / kernel 6.18 / vcan0 UP；
  Python 3.12.3 / isotp 2.0.7 / udsoncan 1.25.2 / pytest 9.0.3；证据见 `report/pytest_vcan_output.txt`）
  - 6 条原始字节路径 + 1 条 **udsoncan 客户端路径**（change_session → unlock_security_access →
    request_download → transfer_data → request_transfer_exit 全肯定响应）
  - 修复记录：① 就绪探测曾把完整响应与 2 字节期望做 `==` 比较（应为 `response[:2]`）；
    ② udsoncan `MemoryLocation` 默认按最小字节自动选格式（ALFID=0x13 → NRC 0x13），
    需显式 `address_format=24, memorysize_format=16` 对齐本仓 0x23 契约。

## 6. 已知局限（如实记录）

1. **未实现逐块哈希 / Merkle**：0x36 的完整性目前只到"块序/长度/越界"层，
   镜像内容级完整性（TC-02）未做——`manifest.image_sm3` 已携带镜像摘要，
   但本实验传输的是 manifest 本身，未传输镜像体。
2. **无 A/B 分区、无断电恢复**（TC-07）：合成 ECU 无分区模型，未模拟断电回滚。
3. **无云端任务 / 网关**（TC-13 / TC-19）：未建模令牌与路由。
4. **版本判定是字符串语义版本**（`x.y.z` 元组比较），不是真实 OEM 的 RXSWIN 模型。
5. **skip ≠ 通过**：vcan0 e2e 在无 vcan0/isotp 的环境中整模块 skip；
   本机已跑通，但其它环境（如纯 Windows）仍会 skip，报告按实际执行环境记录。

## 7. 对齐知识库

- P16（`projects/project-16-ota-security/README`）：本项目实现其测试矩阵中
  「签名错误 / 篡改 / 降级 / 越权 / 分块完整性」五类核心用例（TC-01/03/04/05/06 + 表格外的分块类），
  并保留 TC-02/07/13/15/19 为下一步。
- P11（`project-11-sm234-crypto-test`）：验签环节直接复用 labs/crypto 的 gmcrypto（SM2/SM3），
  两项目按知识库约定交叉引用。

## 下一步

1. 增加镜像体传输 + 逐块 SM3 哈希 / Merkle 根校验（TC-02）；
2. 增加 A/B 分区与断电回滚模拟（TC-07）；
3. 增加更多负向：重放历史包（时间窗/nonce）、验签公钥不可用 fail-closed（TC-15）；
4. udsoncan 路径已覆盖，可扩展 udsoncan 侧的负向断言（NRC 分类）。
