# `configs/mcp/` —— SecurityLab MCP 只读 server 配置

> 契约：`brief/contracts/SECURITYLAB_MCP_V1.md` v1.0.0（FROZEN）
> 实现：`securitylab_mcp/`（只读面）
> 本轮状态：只读 server 已实现；真实 DSH/Fleet 连接 **未连接**；
> `securitylab_vcan` **disabled**；vcan/CAN/ECU/车辆/设备实验 **未运行**。

## 1. 文件

| 文件 | 用途 |
|---|---|
| `securitylab_mcp.example.json` | server 端受信配置示例（workspace roots + report/result 索引） |
| `binding.example.json` | 受信 run/session binding 示例（合成身份，不是真实 run） |

这两个文件都不含凭据。实际使用时由受信 host/Fleet 生成 binding，并确保
worker/模型不能写该文件。

## 2. server 配置

```json
{
  "contract_version": "1.0.0",
  "server": "securitylab_ro",
  "workspaces": {
    "ws_securitylab": {
      "workspace_root": "../..",
      "reports_root": "reports",
      "artifacts_root": "artifacts",
      "reports": {
        "rep_example": { "title": "Report title", "path": "report.md" }
      },
      "run_results": {
        "res_example": {
          "title": "Run result title",
          "path": "runs/run-1.json",
          "status": "completed"
        }
      }
    }
  }
}
```

规则：

- `workspace_root` 必须是已存在的目录；相对路径按 **配置文件所在目录** 解析，
  不做环境变量展开。
- `reports_root` / `artifacts_root` 默认是 `<workspace_root>/reports` 与
  `<workspace_root>/artifacts`，且必须位于 workspace root 内。
- 不同 workspace 的 root 不得互相包含；否则 server 拒绝启动。
- report/result 的 `path` 是相对于对应 root 的 POSIX 路径；必须通过统一路径
  校验、root containment，且不得指向 `.env` / `.git` / `.ssh` / 私钥等受保护路径。
- `report_id` 必须匹配 `rep_[A-Za-z0-9_-]{4,64}`；`result_id` 必须匹配
  `res_[A-Za-z0-9_-]{4,64}`。
- 未知字段、版本不符、非法 root/资源、root 重叠均 fail closed，server 不启动。

## 3. binding 文件（可信身份）

```json
{
  "contract_version": "1.0.0",
  "run_id": "run_00000000000000000000000000000000",
  "role": "researcher",
  "workspace_id": "ws_securitylab",
  "episode_id": "ep_example",
  "tool_profile": "researcher_ro_v1"
}
```

- 身份只能由 host 启动 server 时通过 `--binding` 文件提供；**不从工具参数、
  prompt 或环境变量读取**。
- `role` 与 `tool_profile` 必须匹配
  `brief/contracts/examples/role-preset.registry.json`：
  `researcher_ro_v1`、`vehicle_analyst_ro_v1`、`challenger_ro_v1`、
  `evidence_verifier_ro_v1`、`lab_runner_vcan_v1`、`orchestrator_none_v1`；
  只读角色也接受通用 `securitylab_ro`。
- `lab_runner` 只能绑定 `lab_runner_vcan_v1`，且该 profile 仅暴露 disabled 的
  `securitylab_vcan`。
- `workspace_id` 必须存在于 server 配置中；跨 workspace 资源表现为 `not-found`。

## 4. 启动

```powershell
python -m securitylab_mcp.server `
  --config configs/mcp/securitylab_mcp.example.json `
  --binding configs/mcp/binding.example.json
```

这只是启动本仓库的 **stdio 只读 server**，不会连接 DSH、真实 MCP endpoint、
SecurityLab lab、vcan、CAN/ECU、车辆或任何设备。生产使用时由 Fleet/host 以
独立进程启动并注入可信 binding。

## 5. 安全性质

- 只读：没有写入/删除/shell/Python/命令拼接/sudo/Git 远程写工具。
- 路径：拒绝绝对路径、盘符、UNC、`..`/`.`、NUL、ADS、Windows 保留名；
  `realpath` 后复核 containment，拒绝 symlink/junction escape。
- 输出：单次内容最多 65536 字节；token/api_key/password/secret/authorization/
  cookie/Bearer/Basic/PEM 私钥脱敏；不返回绝对 root 路径。
- 跨 workspace：资源索引按 binding workspace 隔离；不泄露其他 workspace 的
  存在性、版本、路径或统计。
- disabled：`securitylab_vcan` 任何调用返回 `tool-disabled`，
  `meta.executed=false`、`meta.side_effects=0`。

## 6. 离线测试

```powershell
python -m pytest securitylab_mcp/tests -q
python -m mypy securitylab_mcp
```

测试只用 `tmp_path` 下的 synthetic fixtures；不启动 SecurityLab/vcan/设备实验，
不读取凭据，不对路径穿越目标写入。
