# OTA 安全测试矩阵（对齐 P16 · 本仓落地版）

> 判定口径：**PASS = 本机实际执行且通过**；**BLOCKED = 环境缺失未执行（本会话无 isotp/vcan0，WSL 访问 E_ACCESSDENIED）**；
> **NOT_RUN = 超出本仓范围（A/B 分区、云端任务、网关均未建模）**。
> 禁止把 BLOCKED / NOT_RUN 记为通过。

| # | 用例 | 测试项 | 对应测试 | 执行层 | 结果 |
|---|---|---|---|---|---|
| 1 | TC-01 | 攻击者密钥签名 → 拒绝 | `test_attacker_signed_package_rejected` | unit | ✅ PASS |
| 2 | TC-03 | 签名后篡改 manifest 字段（不重签）→ 拒绝 | `test_tampered_manifest_rejected` | unit | ✅ PASS |
| 3 | TC-03b | 篡改签名 1 bit → 拒绝 | `test_tampered_signature_rejected` | unit | ✅ PASS |
| 4 | TC-04 | 攻击者重签 `version=9.9.9` → 死在验签（不是版本判定） | `test_attacker_signed_package_rejected` | unit | ✅ PASS（顺序断言） |
| 5 | TC-05 | 重放同版本合法包 → 拒绝 | `test_equal_version_rejected` | unit | ✅ PASS |
| 6 | TC-06 | 签名完全合法的旧版本包 → 拒绝降级 | `test_downgrade_to_older_signed_package_rejected` | unit | ✅ PASS |
| 7 | TC-06b | 版本单调但低于 manifest 回滚下限 → 拒绝 | `test_below_min_version_rejected` | unit | ✅ PASS |
| 8 | 越权-1 | default session 下 0x34 → `7F 34 7F` | `test_download_rejected_in_default_session` | unit | ✅ PASS |
| 9 | 越权-2 | 编程会话未解锁时 0x34 → `7F 34 33` | `test_download_rejected_when_locked` | unit | ✅ PASS |
| 10 | 分块-丢块 | 首块缺失（BSC 2 先到）→ `7F 36 73` | `test_missing_first_block_rejected` | unit | ✅ PASS |
| 11 | 分块-乱序 | BSC 1 → 3 跳号 → `7F 36 73` | `test_out_of_order_block_rejected` | unit | ✅ PASS |
| 12 | 分块-超长 | data > 126 字节 → `7F 36 13` | `test_oversized_block_rejected` | unit | ✅ PASS |
| 13 | 分块-越界 | 累计超过 0x34 声明 size → `7F 36 13` | `test_overflow_beyond_requested_size_rejected` | unit | ✅ PASS |
| 14 | 分块-幂等 | 完全重复上一条 → `76 <BSC>`、不追加 | `test_duplicate_block_is_idempotent` | unit | ✅ PASS |
| 15 | 分块-未完成 | 0x37 提前 → `7F 37 22`、保持 active | `test_incomplete_transfer_exit_rejected` | unit | ✅ PASS |
| 16 | 分块-无会话 | 无 active 时 0x36 → `7F 36 24` | `test_transfer_data_without_download_rejected` | unit | ✅ PASS |
| 17 | 包结构 | 载荷 < 96 字节 → `7F 37 72`（malformed_package） | `test_malformed_package_rejected` | unit | ✅ PASS |
| 18 | 复位 | 11 01 中止 transfer → 0x36 回 0x24 | `test_reset_aborts_transfer` | unit | ✅ PASS |
| 19 | vcan-功能 | 默认会话 0x34 拒绝（ISO-TP 链路） | `test_vcan_download_rejected_in_default_session` | vcan e2e | ✅ PASS |
| 20 | vcan-功能 | 未解锁 0x34 拒绝 | `test_vcan_download_rejected_when_locked` | vcan e2e | ✅ PASS |
| 21 | vcan-功能 | 合法签名包完整传输 → `77` | `test_vcan_valid_package_accepted` | vcan e2e | ✅ PASS |
| 22 | vcan-功能 | 篡改包 → `7F 37 72` | `test_vcan_tampered_manifest_rejected` | vcan e2e | ✅ PASS |
| 23 | vcan-功能 | 降级包 → `7F 37 72` | `test_vcan_downgrade_rejected` | vcan e2e | ✅ PASS |
| 24 | vcan-功能 | 丢块 → `7F 36 73` | `test_vcan_missing_block_rejected` | vcan e2e | ✅ PASS |
| 25 | vcan-udsoncan | udsoncan 客户端完整刷写流程（change_session → unlock → request_download → transfer_data → exit） | `test_vcan_udsoncan_download_flow` | vcan e2e | ✅ PASS |
| 26 | TC-02 | 镜像篡改（逐块哈希 / Merkle） | — 本仓未实现逐块哈希 | — | ⏸ NOT_RUN（下一步） |
| 27 | TC-07 | 断电中断 / A/B 分区回滚 | — 无分区模型 | — | ⏸ NOT_RUN（下一步） |
| 28 | TC-13 | 升级任务令牌伪造 | — 无云端任务模型 | — | ⏸ NOT_RUN（下一步） |
| 29 | TC-15 | 验签公钥不可用 fail-closed | — 公钥注入固定 | — | ⏸ NOT_RUN（下一步） |
| 30 | TC-19 | 网关路由绕过 | — 无网关模型 | — | ⏸ NOT_RUN（下一步） |

## 覆盖统计

| 层 | 条数 | PASS | BLOCKED | NOT_RUN |
|---|---:|---:|---:|---:|
| 协议层（unit） | 18 | 18 | 0 | 0 |
| vcan0 e2e | 7 | 7 | 0 | 0 |
| 超出本仓范围 | 5 | 0 | 0 | 5 |

## 执行环境与修复记录（2026-11-07）

- **环境**：WSL Ubuntu（kernel 6.18.54.1-microsoft-standard-WSL2），vcan0 已 UP；
  venv `/home/lilike/.venvs/network/bin/python`（3.12.3）含 isotp 2.0.7 / udsoncan 1.25.2 / pytest 9.0.3；
  venv 缺 gmssl → 通过 `PYTHONPATH` 复用 Windows 侧纯 Python gmssl（**未在 venv 安装任何包**）。
- **首次 6 条 e2e 全失败**的根因：就绪探测把**完整响应** `50 01 00 32 00 64` 与 2 字节 `50 01`
  做了 `==` 比较（uds-basic 原测试用的是 `response[:2]`）→ 永远判"未就绪"。已修复。
- **udsoncan 路径**：`MemoryLocation` 默认按最小字节数自动选择格式（size=194 只有 1 字节 →
  ALFID=0x13，与 uds-basic 的 0x23 契约不符 → NRC 0x13）。已显式指定
  `address_format=24, memorysize_format=16` 对齐本仓契约。
- 运行脚本：`scripts/run_vcan_e2e.sh`；证据：`report/pytest_vcan_output.txt`。
