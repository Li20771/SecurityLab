# 冒烟测试 20 项（零硬件版）

> 对齐知识库 **10-vehicle-architecture/04-车载安全机制与验证 §11「冒烟测试 20 项」**。
> 拿到一台车 / 一个模拟平台，先做这 20 条；全部可在纯软件环境完成
> （vcan + python-can + udsoncan + 本地 OTA 模拟）。
>
> 判定纪律：**未执行项写 NOT_RUN 并注明原因**；"本仓可演练映射"只表示有对应实验台，
> 不表示已在合规场景中执行过。

| # | 检查项 | 命令 / 动作（零硬件版） | 判定标准 | 本仓可演练映射 |
|:---:|:---|:---|:---|:---|
| 1 | 总线拓扑可用 | `ip -br link show type vcan` | 至少 1 条选通总线 | vcan0（需 WSL；本会话 BLOCKED） |
| 2 | 报文可达 | `candump vcan0` + `cansend vcan0 123#0011` | 抓到发出的帧 | vcan0（BLOCKED） |
| 3 | 总线负载基线 | `canbusload vcan0@500000` | 基线值已记录 | vcan0（BLOCKED） |
| 4 | ID 全集枚举 | `candump -l vcan0` 持续 10 min | 得到 ID 列表 | vcan0（BLOCKED） |
| 5 | 网关白名单 | 向每条总线发未登记 ID | 下游无该帧 | 本仓无网关模型（NOT_RUN） |
| 6 | 网关内容校验 | 白名单 ID + 越界数据 | 丢弃或下游拒绝 | 本仓无网关模型（NOT_RUN） |
| 7 | 网关速率限制 | `cangen -g 1` 洪泛白名单 ID | 限速/丢弃 + 告警 | 本仓无网关模型（NOT_RUN） |
| 8 | 诊断可达性 | udsoncan 发 `0x22 F190`（读 VIN） | 有合法响应 | `labs/vehicle/uds-basic`（unit 已有 DID 读取用例；vcan e2e 需 vcan0） |
| 9 | 诊断权限矩阵 | Default 会话发 `0x2E` | NRC `0x33/0x7F` | `labs/vehicle/uds-basic`（unit：0x2E 门控用例） |
| 10 | 安全访问种子随机 | 连发 10 次 `0x27 0x01` | 种子每次不同 | `labs/vehicle/uds-basic`（seed_counter 递增；unit 可验证） |
| 11 | 尝试次数限制 | 错误 key 连续尝试 | N 次后 `0x36` 或锁定 | `labs/vehicle/uds-basic`（锁定状态机 unit 用例） |
| 12 | 固件签名存在 | `openssl dgst -verify` 验证固件签名 | 验证通过 | `labs/crypto`（SM2 验签）+ `labs/vehicle/ota`（OTA 包验签） |
| 13 | 篡改检测 | 翻转 1 字节后重验 | 验签失败 | `labs/vehicle/ota`（篡改 manifest / 篡改签名用例，PASS） |
| 14 | 硬编码凭据扫描 | `strings \| grep -iE "PRIVATE KEY\|secret=\|token="` | 无命中 | `labs/crypto`（K04 熵源审计 / 密钥管理检查表） |
| 15 | 证书链校验 | `openssl s_client -verify_return_error` | Verify return code: 0 | 本仓未建模 TLS/PKI（NOT_RUN） |
| 16 | 过期证书拒绝 | 导入过期证书连接 | 拒绝 + Alert | 本仓未建模 TLS/PKI（NOT_RUN） |
| 17 | 日志留存 | 制造安全事件 → 断电 → 读取 | 日志仍在 | 本仓未建模非易失日志（NOT_RUN） |
| 18 | 日志防篡改 | 诊断清除日志 | 拒绝或清除行为被记录 | 本仓未建模日志服务（NOT_RUN） |
| 19 | 降级拒绝 | 提交低版本合法包 | 拒绝 | `labs/vehicle/ota`（降级 / 等版本 / 低于下限用例，PASS） |
| 20 | 中断恢复 | 升级 50% / 80% 断电 → 重启 | 可正常启动，状态一致 | 本仓未建模 A/B 分区（NOT_RUN） |

## 统计

| 状态 | 条数 | 说明 |
|---|---:|---|
| 有对应实验台、可演练（不代表已按合规流程执行） | 9 | #8/9/10/11/12/13/14/19（+ #1–4 需 vcan0，本会话 BLOCKED） |
| 本仓无对应模型（NOT_RUN） | 9 | #5/6/7/15/16/17/18/20 |
| 环境受限（BLOCKED） | 3 | #1–4 的 vcan0 链路（本会话 WSL E_ACCESSDENIED） |

> 注意：这与知识库 10/04 的 20 项一一对应；映射列只说明"用哪个实验台演练"，
> 正式合规测试仍需按 `evidence_template.md` 留证并按
> `gb44495_requirement_matrix.md` / `gb44496_requirement_matrix.md` 的判定准则执行。
