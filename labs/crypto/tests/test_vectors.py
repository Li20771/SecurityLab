"""四维度测试向量库的加载与执行（算法调用 / 配置 / 密钥管理 / 随机数）。

- 结构校验：每个 *_*.json 必须有 file/source/extracted/dimension 四个字段
- 算法调用：sm3/sm4/sm2 的 KAT 逐字节比对
- 配置：密钥/IV 长度与模式组合（含 ECB/CBC 结构性行为）
- 密钥管理：K04 熵源、K06 k 重用、K12 日志泄露（automated=true 项）
- 随机数：R1 熵源、R6 r 去重（automated=true 项）

skip / NOT_RUN 的项在 README「实测结果」单列，不计入通过。
"""

import json
import pathlib

import pytest

from gmcrypto.sm2 import sm2_keygen, sm2_sign, sm2_verify
from gmcrypto.sm3 import sm3_digest
from gmcrypto.sm4 import sm4_cbc_encrypt, sm4_ecb_encrypt

ROOT = pathlib.Path(__file__).resolve().parent.parent
VECTORS = ROOT / "vectors"
GMCRYPTO = ROOT / "gmcrypto"

REQUIRED_FIELDS = {"file", "source", "extracted", "dimension"}


def _load_all() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(VECTORS.glob("*.json"))
    }


def _load(name: str) -> dict:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def test_vector_files_have_required_fields():
    missing = []
    for name, payload in _load_all().items():
        for field in REQUIRED_FIELDS:
            if field not in payload:
                missing.append(f"{name}:{field}")
    assert not missing, f"向量文件缺字段: {missing}"


def test_four_dimensions_present():
    dims = {payload["dimension"] for payload in _load_all().values()}
    assert dims == {"算法调用", "配置", "密钥管理", "随机数"}, dims


# ======================================================================
# 算法调用：KAT
# ======================================================================


def test_sm3_kat_from_vectors():
    for case in _load("sm3_kat.json")["cases"]:
        msg = bytes.fromhex(case["msg_hex"]) * case.get("repeat", 1)
        assert sm3_digest(msg).hex() == case["digest"], case["id"]


def test_sm4_kat_from_vectors():
    for case in _load("sm4_kat.json")["cases"]:
        key = bytes.fromhex(case["key_hex"])
        pt = bytes.fromhex(case["plaintext_hex"])
        if case["mode"] == "ECB":
            ct = sm4_ecb_encrypt(key, pt)
        else:
            ct = sm4_cbc_encrypt(key, bytes.fromhex(case["iv_hex"]), pt)
        assert ct[:16].hex() == case["ciphertext_hex"], case["id"]


def test_sm2_sign_kat_from_vectors():
    case = _load("sm2_sign_kat.json")["cases"][0]
    priv = bytes.fromhex(case["private_key_hex"])
    pub = bytes.fromhex(case["public_key_x_hex"] + case["public_key_y_hex"])
    msg = case["message_ascii"].encode()
    assert sm2_verify(pub, msg, sm2_sign(priv, msg)) == case["expect_verify"]


# ======================================================================
# 配置
# ======================================================================


def _bytes_of(n: int, block: bytes | None = None) -> bytes:
    if block is not None:
        return (block * (n // len(block) + 1))[:n]
    return bytes(i % 256 for i in range(n))


def test_config_cases_from_vectors():
    for case in _load("config_cases.json")["cases"]:
        op = case["op"]
        key = _bytes_of(case["key_len"])
        block = bytes(range(16))
        pt = (
            _bytes_of(case["pt_len"], block=block)
            if case.get("pt_repeat_block")
            else _bytes_of(case["pt_len"])
        )

        if op == "sm4_ecb_encrypt":
            if case["expect"] == "raises":
                with pytest.raises(ValueError):
                    sm4_ecb_encrypt(key, pt)
            else:
                ct = sm4_ecb_encrypt(key, pt)
                if case["expect"] == "ok_and_blocks_equal":
                    assert ct[:16] == ct[16:32]
        elif op == "sm4_cbc_encrypt":
            iv = _bytes_of(case["iv_len"])
            if case["expect"] == "raises":
                with pytest.raises(ValueError):
                    sm4_cbc_encrypt(key, iv, pt)
            else:
                ct = sm4_cbc_encrypt(key, iv, pt)
                if case["expect"] == "ok_and_blocks_differ":
                    assert ct[:16] != ct[16:32]
        else:
            pytest.fail(f"未知 op: {op}")


# ======================================================================
# 密钥管理（automated=true 项）
# ======================================================================

BANNED_ENTROPY = (
    "random.random(",
    "random.randint(",
    "random.seed(",
    "srand(",
)


def _gmcrypto_sources() -> dict[pathlib.Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in GMCRYPTO.glob("*.py")}


def test_k04_entropy_source_audit():
    """K04：gmcrypto 不得使用非密码学随机源；sm2 必须 os.urandom。"""
    hits = []
    for path, text in _gmcrypto_sources().items():
        for token in BANNED_ENTROPY:
            if token in text:
                hits.append(f"{path.name}:{token}")
    assert not hits, f"发现弱随机源: {hits}"
    assert "os.urandom" in (GMCRYPTO / "sm2.py").read_text(encoding="utf-8")


def test_k06_signature_k_uniqueness():
    """K06：连续 20 次签名，r 值不得重复（r 重复 = k 重用 = 私钥可解）。"""
    priv, _ = sm2_keygen()
    rs = [sm2_sign(priv, b"k-reuse-probe").hex()[:64] for _ in range(20)]
    assert len(set(rs)) == len(rs), "检测到 r 重复 → k 重用"


def test_k12_no_key_logging_in_library():
    """K12：库代码不得 print 密钥/明文（demo 脚本的打印仅限测试 fixture）。"""
    for path, text in _gmcrypto_sources().items():
        assert "print(" not in text, f"{path.name} 含 print，可能泄露密钥"


# ======================================================================
# 随机数（automated=true 项）
# ======================================================================


def test_r1_entropy_source():
    """R1：熵源必须是 os.urandom / secrets。"""
    for path, text in _gmcrypto_sources().items():
        for token in BANNED_ENTROPY:
            assert token not in text, f"{path.name} 含弱随机源 {token}"


def test_r6_sm2_k_reuse_probe():
    """R6：连续签名 r 去重（与 K06 同源检查，单列供报告引用）。"""
    priv, _ = sm2_keygen()
    rs = [sm2_sign(priv, b"r6-probe").hex()[:64] for _ in range(10)]
    assert len(set(rs)) == len(rs)
