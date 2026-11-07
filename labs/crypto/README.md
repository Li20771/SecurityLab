# labs/crypto —— SM2/SM3/SM4 国密实验台（知识库 P11 落点）

**定位**：密码模块测试方向的抓手——零硬件、纯软件、可复现的国密算法调用与测试向量库。

**范围**：
- SM2：生成密钥对 → 签名 → 验签通过；篡改消息/签名/换公钥 → 验签失败
- SM3：字节串与文件摘要（标准 KAT）；SM3-MAC（教学用简单构造）
- SM4：ECB / CBC 加解密往返；标准 KAT
- 四维度测试向量库骨架：算法调用 / 配置 / 密钥管理 / 随机数

**红线**：只做本机软件实验，不接触任何真实密码设备/HSM。

---

## 1. 依赖

```text
# requirements.txt（本机验证版本，2026-11-07）
gmssl==3.2.2     # 纯 Python 的 SM2/SM3/SM4 实现
pytest==9.0.3
```

```powershell
python -m pip install -r requirements.txt
```

> 说明：`gmssl` 是 **Python 包**，不是"国密 OpenSSL 命令行"；两者同名不同物。

## 2. 目录结构

```text
labs/crypto/
├── README.md                  # 本文件：依赖 / 用法 / 实测结果
├── requirements.txt
├── conftest.py                # 把 labs/crypto 加入 sys.path
├── demo_crypto.py             # 可复现演示脚本（SM2/SM3/SM4 全链路）
├── gmcrypto/                  # 被测模块（薄封装，接口收敛 + SM2 密钥生成）
│   ├── __init__.py
│   ├── sm2.py                 # keygen / sign / verify（含 SM2 曲线标量乘法）
│   ├── sm3.py                 # digest / mac
│   └── sm4.py                 # ecb / cbc 加解密
├── tests/
│   ├── test_sm2.py            # 签名验签 roundtrip + 标准公钥 KAT + 负向
│   ├── test_sm3.py            # KAT + MAC
│   ├── test_sm4.py            # KAT + ECB/CBC roundtrip
│   └── test_vectors.py        # 四维度向量库加载与执行
├── vectors/                   # 测试向量库骨架（四维度）
│   ├── README.md              # 向量库约定与扩展方式
│   ├── sm3_kat.json           # 算法调用（标准 KAT）
│   ├── sm4_kat.json           # 算法调用（标准 KAT）
│   ├── sm2_sign_kat.json      # 算法调用（GM/T 0003.5 密钥对）
│   ├── config_cases.json      # 配置（密钥/IV 长度、模式组合）
│   ├── key_management_checks.json  # 密钥管理（13 项检查表）
│   └── randomness_checks.json # 随机数（R1–R11 裁剪）
└── report/
    ├── pytest_output.txt      # 实测 pytest 输出
    └── demo_output.txt        # 实测演示脚本输出（每次运行追加）
```

## 3. 用法

```powershell
cd labs/crypto

# 1) 全量测试（26 条）
python -m pytest tests/ -v --tb=short

# 2) 可复现演示（SM2 签名/验签 → SM3 摘要 → SM4 ECB/CBC；输出追加到 report/demo_output.txt）
python demo_crypto.py

# 3) 只跑向量库
python -m pytest tests/test_vectors.py -v
```

## 4. 实测结果

**测试**：`26 passed in 1.14s`（Python 3.14.4 / pytest 9.0.3 / gmssl 3.2.2，2026-11-07）

| 组 | 覆盖 | 结果 |
|---|---|---|
| test_sm2.py（6） | 密钥生成范围、标准公钥 KAT（d·G 与 GM/T 0003.5 逐字节一致）、签名验签 roundtrip、篡改消息/错误公钥/篡改签名 → 失败 | 全过 |
| test_sm3.py（5） | KAT（"abc"/空串/512 位）、确定性、MAC 往返 | 全过 |
| test_sm4.py（4） | 标准 ECB KAT（取首块 `681edf34…`）、ECB/CBC 往返、PKCS#7 任意长度行为 | 全过 |
| test_vectors.py（11） | 四维度结构校验、KAT 从文件加载执行、配置组合、K04/K06/K12、R1/R6 | 全过 |

**演示脚本**：`===== result: PASS =====`（实测输出见 `report/demo_output.txt`）

```text
[SM2] verify(original)  : True
[SM2] verify(tampered)  : False  (必须 False)
[SM3] digest(bytes)     : 782f9221e817c4eaa03d5a7cabd6830242ade7b4bc33d984506cd478b7145110
[SM4] ECB roundtrip ok  : True
[SM4] CBC roundtrip ok  : True
===== result: PASS =====
```

> 演示脚本每次运行生成**临时测试密钥对**（打印在输出里），仅用于演示流程；
> 它不是凭据，也不会被任何其它组件使用。

### 向量库四维度现状

| 维度 | 条目 | 已自动执行 | 未执行 / NOT_RUN |
|---|---|---|---|
| 算法调用 | SM3 ×3、SM4 ×2、SM2 ×1 | 全部 | — |
| 配置 | 7 | 全部 | — |
| 密钥管理 | 13 | K04 / K06 / K12 | 其余 10 项为人工检查表（形态局限见下） |
| 随机数 | R1/R3/R5/R6/R7/R11 | R1 / R6 | R3 / R5 / R7 记 NOT_RUN（进程内无法冷启动 / IV 由调用方提供 / 无持久化计数器） |

## 5. 已知局限（如实记录）

1. **gmssl 的 SM4 强制 PKCS#7 填充**：`crypt_ecb` 对任意长度（含 0、17、33 字节）都静默填充，
   不抛"长度非法"。所以"非分组倍数必须报错"的预期**对本后端不成立**——
   这是被测件行为，已写入 `test_sm4_ecb_pads_any_length` 的 docstring 并记录。
2. **SM2 默认用户 ID**：gmssl `sign_with_sm3` 内部使用默认 ID `1234567812345678`，
   不支持自定义 ID；本实验按默认 ID 做 roundtrip/KAT，ID 差异属互通性测试范围（后续块）。
3. **软件库形态局限**：密钥在进程地址空间内可读，这是"软件库"形态的设计下限
   （HSM/SE 形态才要求不可导出）；密钥管理表中 K02/K07/K08/K09/K10/K11/K13
   因此标为"形态局限/不适用"，不当作缺陷。
4. **SKIP ≠ 通过**：向量库中 `automated: false` 的条目是人工检查表骨架，
   执行时在报告里逐条给判定，未执行项单列 NOT_RUN。

## 6. 对齐知识库

- P11（`projects/project-11-sm234-crypto-test/README`）：本项目是其"最小可运行落点"——
  向量套件骨架 + 接口测试脚本 + 四维度检查表；负向/互通/性能矩阵是它的后续扩展项。
- 05/07（`05-cryptography/07-密码模块测试方法`）：四维度 = 算法调用（KAT）、配置（密钥/IV 长度）、
  密钥管理（§2.6 十三项）、随机数（§2.7 R 系列）的裁剪落地。

## 下一步

- 负向用例扩充到 ≥24 条并统计拒绝率（对齐 P11 的交付物③）
- 互通矩阵：gmssl × 标准向量 ×（可选）国密 OpenSSL / 厂商后端
- 性能基线（吞吐 + P50/P95）与 SM4-CMAC（SecOC 形态）
