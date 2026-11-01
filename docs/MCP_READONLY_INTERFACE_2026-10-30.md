# SecurityLab MCP 只读接口（v1.0.0）

**日期：** 2026-10-30
**基线：** `c54f94fdec350e40998a2ba8b151306334010c61`
**契约：** `brief/contracts/SECURITYLAB_MCP_V1.md` v1.0.0（FROZEN）
**实现：** `securitylab_mcp/`（Python，stdlib only）

## 1. 状态

| 项 | 状态 | 说明 |
|---|---|---|
| `securitylab_ro` 只读 MCP server | `IMPLEMENTED` | stdio JSON-RPC；离线 fake/synthetic 测试通过 |
| 真实 DSH/Fleet 连接 | `IMPLEMENTED BUT NOT CONNECTED` | 未启动真实 DSH、未连接 Workbench fake 之外的 endpoint |
| `securitylab_vcan` | `DISABLED` | 仅保留 schema/结果路径；任何调用 `tool-disabled`，无执行 |
| 真实 MCP client / DSH runtime probe | `NOT_RUN` | 本轮不运行 |
| vcan / CAN / ECU / 车辆 / 设备实验 | `NOT_RUN` | 本轮不启动、不连接、不执行 |
| 真实 report/result 索引 | `IMPLEMENTED` | 示例配置索引既有 `reports/` 文件；未运行实验生成新产物 |

## 2. 工具面

| tool | 参数 | 行为 |
|---|---|---|
| `lab_catalog` | `{}` | 列出绑定 workspace 的 report/run-result 逻辑 ID 与三个 file root；最多 100 项 |
| `lab_status` | `{}` | 返回静态 `{status:"ready", execution:"disabled", vcan:"disabled"}`；不探测环境 |
| `lab_read_file` | `{"path":"<relative>","root_id?":"workspace\|reports\|artifacts"}` | 读取 root 内 UTF-8 文本；bounded/redacted |
| `lab_get_report` | `{"report_id":"rep_…"}` | 按配置索引读取既有报告；不执行任何东西 |
| `lab_get_run_result` | `{"result_id":"res_…"}` | 按配置索引读取既有 run result；不重跑 |
| `securitylab_vcan` | `{"suite":"unit_smoke\|virtual_smoke"}` | **disabled**；调用返回 `tool-disabled` |

不提供：任意 shell/Python、命令拼接、sudo、写/删除、workspace 外路径、
Git push/merge、远程目标、真实 CAN/ECU/车辆操作、任意 suite/目标/ECU 地址。

`lab_runner` binding 只用于验证 disabled schema/拒绝路径；本仓库**不提供**
`securitylab_vcan` server 进程，也不会启动/连接/运行它。当前唯一可启动的
server 是 `server: securitylab_ro`。

## 3. 结构

```text
securitylab_mcp/
  binding.py     # role/profile/allowlist + 可信 binding 校验
  config.py      # workspace roots + report/result 索引 + read_allowlist
  paths.py       # 相对路径校验、containment、symlink 复核、有界读取、denylist
  redaction.py   # 结构化 JSON/YAML/INI/ENV + Bearer/Basic + PEM 脱敏
  service.py     # 五个只读工具 + disabled vcan；每次调用独立授权检查
  server.py      # MCP stdio JSON-RPC（initialize/tools/list/tools/call）
  contracts/     # brief/contracts v1.0.0 两个 schema 的只读镜像
  tests/         # tmp_path synthetic fixtures，离线
```

## 4. 可信身份与配置

- `role`、`workspace_id`、`run_id`、`episode_id`、`tool_profile` 只来自
  server 启动参数 `--binding <trusted-file>`；不从 `tools/call` 参数、prompt
  或环境变量读取。
- server 配置 `--config <trusted-file>` 固定 workspace root、reports/artifacts
  root 与 report/result 索引；相对路径按配置文件目录解析。
- 模型在工具参数中放 `role`/`workspace_id`/`run_id`/`session_id`/
  `tool_profile`/`approval_ref`/`command`/`shell`/`python`/`sudo`/`target`/
  `env` 等字段 → `identity-in-args`，无副作用。
- role → tool allowlist 与 `brief/contracts/examples/role-preset.registry.json`
  一致；`challenger` 没有 `lab_get_run_result`；`lab_runner` 只有 disabled vcan。

## 5. 服务端强制检查

1. **Binding**：role/profile 必须匹配；workspace 必须已登记；未知/版本不符
   fail closed（server 退出码 2）。
2. **工具 allowlist**：profile 外/未知工具 → `tool-not-allowed`；
   `securitylab_vcan` → `tool-disabled`，`executed=false`、`side_effects=0`。
3. **参数**：严格 schema；未知字段、缺失、类型/格式/root_id 非法 →
   `invalid-args`；identity/permission 字段 → `identity-in-args`。
4. **路径**：root 相对 POSIX 路径；拒绝绝对路径、盘符、UNC、`..`/`.`、
   NUL/控制字符、ADS、Windows 保留名、尾随点/空格、空段。
5. **Containment**：词法 containment + `realpath` 复核；symlink/junction
   逃逸 → `symlink-escape`。
6. **敏感路径**：`.git`、`.ssh`、`.dsh`、`credentials/`、`secrets/`、`.env*`、
   私钥后缀/常见私钥文件名，以及 `.aws`/`.kube`/`.docker`/`.gnupg`/`.azure`
   目录、`.netrc`/`.git-credentials`/`.npmrc`/`.pypirc`/`.htpasswd`/`.pgpass`/
   `kubeconfig` 等凭据文件 → `not-found`（不泄露存在性）；server config /
   binding 文件本身也不通过 `lab_read_file` 暴露。denylist 是 defense-in-depth，
   不是完整 ACL。
7. **跨 workspace**：绑定 workspace 之外的 report/result/file 表现为
   `not-found` / `path-out-of-root`；catalog 只列绑定 workspace 资源，不含绝对 root。
8. **输出上限**：内容最多 65536 UTF-8 字节；超限截断并
   `data.truncated=true`、`data.bytes=65536`；catalog ≤ 100 项。
9. **脱敏**：token/api_key/apikey/password/secret/authorization/cookie 等
   键值（含 JSON 引号键、YAML/INI/ENV、嵌套对象与含空格/转义值）、
   `Bearer`/`Basic` 凭据、PEM 私钥块 → `[REDACTED]`；catalog/report/result
   的 title/status 同样脱敏；`meta.redacted=true`。见 §9。
10. **不泄露内部**：错误消息固定、无绝对路径/栈/环境值；MCP JSON-RPC 内部错误
    只返回通用消息。

错误码与 contract 1.0.0 一致；成功/失败信封字段、`meta.identity_source =
"server-binding"`、`meta.side_effects = 0` 由测试校验。

## 6. 与 Workbench fake client 的对齐

为便于跨仓库 contract 测试，以下行为与
`01-workbench/fleet/src/mcp/securitylab-client.ts` 对齐：

- `lab_status` 数据为 `status/execution/vcan` 三字段；
- catalog 含 report/run-result 与 `workspace`/`reports`/`artifacts` 三个 file root；
- 路径拒绝原因映射为 `path-out-of-root`，symlink 逃逸为 `symlink-escape`；
- 截断阈值 65536，ASCII 内容截断后长度为 65536；
- identity/permission 参数键集合包含 `command`/`shell`/`python`/`sudo`/
  `target`/`env` 等。

真实 Wire 层返回 MCP `content` 文本信封；协议 `2025-06-18` 额外提供
`structuredContent`，旧协议版本（如 `2024-11-05`）只返回 `content`。JSON-RPC
仅用于协议错误（未知 method、parse error），工具级拒绝走 contract 信封 +
`isError`。

## 7. 测试矩阵（本轮实际执行）

- Python 3.14.4 / pytest 9.0.3 / mypy 2.1.0（本机）。
- 2026-10-30 本轮实际结果：

| 检查 | 命令 | 结果 |
|---|---|---|
| MCP 离线测试 | `python -m pytest securitylab_mcp/tests -q` | `PASS`：153 passed, 4 skipped |
| 类型检查 | `python -m mypy securitylab_mcp` | `PASS`：17 source files |
| 语法编译 | `python -m compileall -q securitylab_mcp` | `PASS` |
| lint | `uvx --from ruff==0.16.8 ruff check securitylab_mcp` | `BLOCKED`：工具未安装，下载被网络/TLS 阻断 |
| 示例配置加载 + stdio `tools/list` | 向 `python -m securitylab_mcp.server --config configs/mcp/securitylab_mcp.example.json --binding configs/mcp/binding.example.json` 的 stdin 发送一行 `tools/list` | `PASS`（只读 server 启动，无外部连接） |
| 真实 DSH/MCP/vcan/设备 | — | `NOT_RUN` |

`SKIPPED` 的 4 项是真实 symlink 创建测试：当前 Windows 主机未授予创建
symlink 的权限。symlink containment 逻辑另由「注入 realpath 映射」的纯计算
测试覆盖并通过；Linux CI 上真实 symlink 测试会实际执行。`lab_runner` /
`securitylab_vcan` 的 disabled 路径已测试，但未启动任何 vcan/设备。

测试覆盖：允许读取、非法/越界路径、symlink 计算与（本机权限允许时）真实
symlink、identity 参数、未知/disabled 工具、跨 workspace、输出上限、脱敏、
report/result 只读、binding/config fail closed、stdio initialize/list/call、
JSON-RPC 协议错误。

## 8. 明确未完成 / 未验证

- 未连接真实 DSH Desktop/SDK、未启动真实 DSH session、未连接 Workbench
  Fleet 的真实 MCP client；跨仓库集成测试由 Workbench 侧 fake endpoint 完成。
- 未启动/连接/运行任何 vcan、SocketCAN、CAN/ECU、车辆或物理设备测试。
- 未读取/复制 credential/token/key 文件或环境变量值。
- 未 push/merge，未修改工作区外的原始仓库或冻结 brief。
- 真实 runtime/实验验收保持 `NOT_RUN`；fake 测试通过不等于真实集成通过。

## 9. Round-2 secret-safety hardening（2026-11-01）

### 9.1 结构化脱敏范围

`redact_text` 在文本层面覆盖以下形式，替换为 `[REDACTED]` 并保留原引号风格：

- JSON 风格的带引号键/值：`{"token":"..."}`、嵌套对象/数组、
  含空格的值 `{"password": "my secret"}`、转义引号 `{"secret":"a\"b"}`，
  以及 pretty-print 中值换到下一行的形式 `"token":\n  "..."`；
- YAML / INI / ENV：`password: "..."`、`token = '...'`、`TOKEN=...`，
  以及无引号形式（无引号值允许空格，止于行尾/右括号）；
- 键名（大小写不敏感，带词边界）：`token`、`access_token`、
  `refresh_token`、`api_key`、`apikey`、`api-key`、`password`、`passwd`、
  `pwd`、`secret`、`client_secret`、`authorization`、`proxy-authorization`、
  `cookie`、`set-cookie`、`private_key`；
- `Bearer` / `Basic` 凭据与 PEM 私钥块。

`tokenizer`、`tokens`、`token_count` 等普通词不会因包含 `token` 而误判。
脱敏是 fail-closed 的：内部异常向上传播，service 返回 `redaction-failed`，
不会回退为未脱敏内容。输出仍是文本级脱敏 + 敏感 denylist 的 defense-in-depth，
**不是**“绝无秘密”的完整保证；真正的边界仍是最小授权 root 与 allowlist。

### 9.2 `read_allowlist` 配置语义

每个 workspace 可选一个 `read_allowlist`：

```json
"read_allowlist": ["notes/", "reports/2026/", "README.md"]
```

- 最多 200 个 root 相对 POSIX 路径前缀；目录条目允许一个尾随 `/`，
  解析时先去掉再走统一的相对路径策略。
- 绝对路径、盘符、UNC、`..`/`.`、控制字符、ADS、Windows 保留名以及
  敏感路径（如 `.env`、`.ssh/...`、`*.pem`）一律 `ConfigError`，server 不启动。
- 字段省略或为空数组表示**不增加**额外限制（与既有配置向后兼容）；
  显式 `null` 等非数组值 fail closed。未改变 contract version。
- 非空时，`lab_read_file` 只允许路径精确等于某项，或位于 `entry + "/"`
  之下；其余一律 `not-found`，且不回显 allowlist 内容。
- 词法检查之后，服务端还会对 realpath 后的 canonical root-relative 路径
  再检查一次 allowlist；`allowed-prefix/link -> other-prefix/file` 的
  symlink 不能借允许前缀读取非允许文件。
- `lab_get_report` / `lab_get_run_result` 按显式注册的 ID 读取，不受
  allowlist 影响。
- 读取时仍执行 canonical + realpath root containment，symlink/junction
  不能逃逸绑定 root。

### 9.3 Sensitive-path denylist

`paths.py` 在既有 `.git`/`.ssh`/`.dsh`/`credentials`/`secrets`/`.env*`/
私钥后缀之外，增加 `.aws`、`.kube`、`.docker`、`.gnupg`、`.azure` 目录，
`.netrc`、`.git-credentials`、`.npmrc`、`.pypirc`、`.htpasswd`、`.pgpass`、
`kubeconfig` 文件，以及 `.kdbx`、`.jks`、`.keystore`、`.ovpn`、`.p8`
后缀。该 denylist 是 **defense-in-depth，不是完整 ACL**；普通 report/
artifact 文件不受影响，生产 profile 仍需最小 root + allowlist。

### 9.4 vcan

`securitylab_vcan` 保持 **disabled**：只保留 schema，任何调用返回
`tool-disabled`，`executed=false`、`side_effects=0`；本轮未启动、未连接、
未探测任何 vcan/CAN/ECU/车辆/设备。

### 9.5 Round-2 验证（离线 synthetic）

| 检查 | 命令 | 结果 |
|---|---|---|
| MCP 离线测试 | `python -m pytest securitylab_mcp/tests -q` | `PASS`：238 passed, 5 skipped |
| lint（指定入口） | `python -m ruff check securitylab_mcp` | `BLOCKED`：本机 Python 未安装 ruff |
| lint（fallback） | `uvx --from ruff==0.16.8 ruff check securitylab_mcp` | `PASS`：All checks passed |
| 类型检查 | `python -m mypy securitylab_mcp` | `PASS`：18 source files |
| whitespace | `git diff --check` | `PASS` |
| 真实 DSH/MCP/vcan/设备 | — | `NOT_RUN` |

5 个 skip 仍是 Windows 主机不允许创建 symlink 的真实 symlink 用例（含
allowlist canonical 复核用例）；canonical 复核逻辑另由 monkeypatch 单元测试
覆盖。所有新增测试只用 `tmp_path` synthetic fixture 与 `SENTINEL` 值，未读取
真实凭据，未连接任何 lab/vcan/设备。
