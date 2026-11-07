# labs/crypto 测试向量库骨架

四个维度各占一个文件（或一组文件），骨架先立结构、再逐步填满：

| 维度 | 文件 | 内容 | 状态 |
|---|---|---|---|
| 算法调用 | `sm3_kat.json` / `sm4_kat.json` / `sm2_sign_kat.json` | 标准 KAT，逐字节比对 | 已填 3 组真实向量 |
| 配置 | `config_cases.json` | 密钥/IV 长度、模式组合、ECB/CBC 结构性行为 | 已填 7 条可执行用例 |
| 密钥管理 | `key_management_checks.json` | 对齐知识库 05/07 §2.6 的 13 项检查 | 3 项自动执行 + 10 项人工检查表 |
| 随机数 | `randomness_checks.json` | 对齐知识库 05/07 §2.7 的 R1–R11 裁剪 | 2 项自动执行 + NOT_RUN 项注明原因 |

## 约定

1. **每个文件必须有** `file` / `source` / `extracted` / `dimension` 四个字段——
   这是"向量可溯源"的证据纪律（不写来源的向量不算证据）。
2. 向量来源优先标准正文附录（GB/T 32905-2016、GB/T 32907-2016、GM/T 0003.5-2012），
   条号写 `§[条款号]` 并注明"以正式文本为准"。
3. `automated: true` 的条目由 `tests/test_vectors.py` 真正执行；
   `automated: false` 的是人工检查表条目，报告里逐条给判定与证据。
4. **skip / NOT_RUN ≠ 通过**：未执行项在 README 的实测结果里单列。

## 加载方式

`tests/test_vectors.py` 负责：

- 校验每个文件的结构字段齐全；
- 校验四个维度都存在；
- 执行 KAT / 配置 / 密钥管理 / 随机数里 `automated: true` 的条目。

扩展方式：新增 `*.json` 并带上四个必填字段 + `dimension` 即可被加载；
需要执行的条目加进对应维度的 `automated: true` 分支。
