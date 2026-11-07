"""SM2 密钥对生成 / 签名 / 验签（GM/T 0003.5-2012）。

gmssl 3.2.2 只提供签名与验签，不提供密钥对生成。本模块补齐：

- SM2 推荐曲线（GM/T 0003.5-2012 §1）上的椭圆曲线点加与标量乘法
- ``sm2_keygen()``：``os.urandom`` 取私钥 d ∈ [1, n-1]，公钥 P = d·G

签名与验签本体走 gmssl 的 ``CryptSM2.sign / verify``（内部含 SM3-ZA，
默认用户 ID ``1234567812345678``）。公钥格式为 64 字节 ``x ‖ y``
（不带 04 前缀，与 gmssl 的 hex 约定一致）。
"""

import os

from gmssl import sm2, func

__all__ = ["sm2_keygen", "sm2_sign", "sm2_verify"]

# SM2 推荐曲线参数（GM/T 0003.5-2012 推荐曲线参数）
_P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
_A = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
_N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
_GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
_GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0

_PRIVATE_KEY_LENGTH = 32
_PUBLIC_KEY_LENGTH = 64


def _inv_mod(value: int, modulus: int) -> int:
    """模逆元（Fermat 小定理，modulus 为素数）。"""
    return pow(value, modulus - 2, modulus)


def _ec_add(p1, p2):
    """SM2 曲线上的点加。None 表示无穷远点。"""
    if p1 is None:
        return p2
    if p2 is None:
        return p1

    x1, y1 = p1
    x2, y2 = p2

    if x1 == x2:
        if (y1 + y2) % _P == 0:
            return None  # P + (-P) = O
        # 倍点
        lam = (3 * x1 * x1 + _A) * _inv_mod(2 * y1, _P) % _P
    else:
        lam = (y2 - y1) * _inv_mod((x2 - x1) % _P, _P) % _P

    x3 = (lam * lam - x1 - x2) % _P
    y3 = (lam * (x1 - x3) - y1) % _P
    return (x3, y3)


def _ec_mul(scalar: int, point):
    """SM2 曲线上的标量乘法 k·P（double-and-add）。"""
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = _ec_add(result, addend)
        addend = _ec_add(addend, addend)
        scalar >>= 1
    return result


def sm2_keygen() -> tuple[bytes, bytes]:
    """生成 SM2 密钥对，返回 ``(private_key, public_key)``。

    - private_key：32 字节，d ∈ [1, n-1]
    - public_key：64 字节，x ‖ y（无 04 前缀）
    """
    while True:
        d = int.from_bytes(os.urandom(32), "big")
        if 1 <= d < _N:
            break

    pub = _ec_mul(d, (_GX, _GY))
    private_key = d.to_bytes(32, "big")
    public_key = pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big")
    return private_key, public_key


def sm2_sign(private_key: bytes, data: bytes) -> bytes:
    """SM2 签名（gmssl 内部含 SM3-ZA，默认用户 ID），返回 64 字节 r‖s。"""
    if len(private_key) != _PRIVATE_KEY_LENGTH:
        raise ValueError(f"SM2 私钥必须 32 字节，实际 {len(private_key)} 字节")

    crypt = sm2.CryptSM2(public_key="", private_key=bytes(private_key).hex())
    k = func.random_hex(64)  # 每次签名取新随机 k（k 重用 = 私钥泄露）
    signature_hex = crypt.sign(bytes(data), k)
    return bytes.fromhex(signature_hex)


def sm2_verify(public_key: bytes, data: bytes, signature: bytes) -> bool:
    """SM2 验签。public_key 为 64 字节 x‖y，signature 为 64 字节 r‖s。"""
    if len(public_key) != _PUBLIC_KEY_LENGTH:
        raise ValueError(f"SM2 公钥必须 64 字节，实际 {len(public_key)} 字节")

    crypt = sm2.CryptSM2(public_key=bytes(public_key).hex(), private_key="")
    try:
        return bool(crypt.verify(bytes(signature).hex(), bytes(data)))
    except Exception:
        # gmssl 对畸形签名（长度错/非 hex）可能抛异常；验签失败统一按 False 处理
        return False
