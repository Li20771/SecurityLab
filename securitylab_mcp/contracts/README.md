# Contract schema mirror

这两个 JSON Schema 是 `brief/contracts/` 中 **SecurityLab MCP v1.0.0（FROZEN）**
的只读镜像，供本仓库离线测试和 self-check 使用：

| 文件 | 来源 | SHA-256（冻结时） |
|---|---|---|
| `securitylab-tool-call.v1.schema.json` | `brief/contracts/securitylab-tool-call.v1.schema.json` | `92da8139923a7b5132c0add2b977ba33d0928149d1854ecc08ca3c592dfe21a0` |
| `securitylab-tool-result.v1.schema.json` | `brief/contracts/securitylab-tool-result.v1.schema.json` | `2c491dc9884bf61b80fa4d010bbeccfbd020d7cb16ef152c483a892c8c244b4c` |

规则：

- 不得单方面修改这些文件；契约变化必须先由 Workbench 更新
  `brief/contracts/`，再同步本镜像、代码和测试。
- `securitylab_mcp/tests/test_contract.py` 固定校验上述 SHA-256；镜像漂移会
  使测试失败（fail closed）。
