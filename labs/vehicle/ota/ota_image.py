"""OTA 升级包（合成）：manifest + SM2 签名 / 验签。

包格式（本实验 profile）::

    payload = canonical_manifest_json + signature(64B)

manifest 字段：package_id / version / min_version / image_sm3 / issued_at。
签名覆盖 manifest 的**规范 JSON 字节**（sort_keys + 紧凑分隔符），
验签必须用同一序列化，否则跨实现必失败。

本模块不接触真实固件：image 只是合成字节串，image_sm3 是其 SM3 摘要；
签名/验签复用 labs/crypto 的 gmcrypto（P11 × P16 交叉引用）。
"""

import json
import pathlib
import sys

_CRYPTO_DIR = pathlib.Path(__file__).resolve().parents[2] / "crypto"
if str(_CRYPTO_DIR) not in sys.path:
    sys.path.insert(0, str(_CRYPTO_DIR))

from gmcrypto.sm2 import sm2_sign, sm2_verify  # noqa: E402
from gmcrypto.sm3 import sm3_digest  # noqa: E402

__all__ = [
    "SIGNATURE_LENGTH",
    "canonical_manifest_bytes",
    "build_manifest",
    "build_package",
    "split_package",
    "verify_manifest",
    "parse_version",
]

SIGNATURE_LENGTH = 64

#: 合法包的最小长度：32 字节 manifest + 64 字节签名。
MIN_PACKAGE_LENGTH = 96


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """manifest 的规范字节（签名与验签必须使用同一序列化）。"""
    return json.dumps(
        manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def build_manifest(
    version: str,
    min_version: str,
    image: bytes,
    *,
    package_id: str = "demo-pkg-001",
    issued_at: str = "2026-11-07T00:00:00+08:00",
) -> dict:
    """构造 manifest：版本 / 回滚下限 / 镜像 SM3 摘要。"""
    return {
        "package_id": package_id,
        "version": version,
        "min_version": min_version,
        "image_sm3": sm3_digest(bytes(image)).hex(),
        "issued_at": issued_at,
    }


def build_package(manifest: dict, private_key: bytes) -> bytes:
    """manifest + SM2 签名（64B）拼成完整 OTA 传输载荷。"""
    body = canonical_manifest_bytes(manifest)
    return body + sm2_sign(private_key, body)


def split_package(payload: bytes) -> tuple[dict, bytes]:
    """把载荷拆成 ``(manifest, signature)``；结构非法抛 ``ValueError``。"""
    if len(payload) < MIN_PACKAGE_LENGTH:
        raise ValueError(
            f"包长度 {len(payload)} < {MIN_PACKAGE_LENGTH}（manifest 至少 32B + 签名 64B）"
        )

    body, signature = payload[:-SIGNATURE_LENGTH], payload[-SIGNATURE_LENGTH:]

    try:
        manifest = json.loads(body.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest 不是合法 JSON: {exc}") from None

    if not isinstance(manifest, dict):
        raise ValueError("manifest 必须是 JSON 对象")

    for key in ("package_id", "version", "min_version", "image_sm3"):
        if key not in manifest:
            raise ValueError(f"manifest 缺字段: {key}")

    return manifest, signature


def verify_manifest(manifest: dict, signature: bytes, public_key: bytes) -> bool:
    """验证 manifest 的 SM2 签名（公钥为 64B x‖y）。"""
    return sm2_verify(public_key, canonical_manifest_bytes(manifest), signature)


def parse_version(version: str) -> tuple[int, ...]:
    """把 ``'x.y.z'`` 解析成可比较元组；非法格式抛 ``ValueError``。"""
    parts = str(version).split(".")
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"版本号格式非法: {version!r}")
    return tuple(int(part) for part in parts)
