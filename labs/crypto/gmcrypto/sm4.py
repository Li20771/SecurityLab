"""SM4 分组密码 ECB/CBC（GB/T 32907-2016）。

算法本体来自 gmssl 3.2.2。gmssl 的 ``crypt_ecb`` / ``crypt_cbc``
自动做 PKCS#7 填充（16 字节进、32 字节出），本模块保留该行为并在
docstring 里写明，KAT 用例按"取首 16 字节 = 单块 ECB 结果"的方式对齐
标准向量（PKCS#7 在 16 字节明文后追加整块 0x10 填充，首块密文即
单块 ECB 结果）。
"""

from gmssl.sm4 import SM4_DECRYPT, SM4_ENCRYPT, CryptSM4

__all__ = [
    "sm4_ecb_encrypt",
    "sm4_ecb_decrypt",
    "sm4_cbc_encrypt",
    "sm4_cbc_decrypt",
]

_KEY_LENGTH = 16
_IV_LENGTH = 16


def _check_key(key: bytes) -> None:
    if len(key) != _KEY_LENGTH:
        raise ValueError(f"SM4 密钥必须 16 字节，实际 {len(key)} 字节")


def _check_iv(iv: bytes) -> None:
    if len(iv) != _IV_LENGTH:
        raise ValueError(f"SM4 CBC IV 必须 16 字节，实际 {len(iv)} 字节")


def sm4_ecb_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """SM4-ECB 加密（PKCS#7 填充，输出长度 = 输入向上取整到 16 字节倍数 + 16）。"""
    _check_key(key)
    cipher = CryptSM4()
    cipher.set_key(bytes(key), SM4_ENCRYPT)
    return bytes(cipher.crypt_ecb(bytes(plaintext)))


def sm4_ecb_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    """SM4-ECB 解密（去 PKCS#7 填充，非法填充由 gmssl 抛异常）。"""
    _check_key(key)
    cipher = CryptSM4()
    cipher.set_key(bytes(key), SM4_DECRYPT)
    return bytes(cipher.crypt_ecb(bytes(ciphertext)))


def sm4_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """SM4-CBC 加密（PKCS#7 填充）。"""
    _check_key(key)
    _check_iv(iv)
    cipher = CryptSM4()
    cipher.set_key(bytes(key), SM4_ENCRYPT)
    return bytes(cipher.crypt_cbc(bytes(iv), bytes(plaintext)))


def sm4_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """SM4-CBC 解密（去 PKCS#7 填充）。"""
    _check_key(key)
    _check_iv(iv)
    cipher = CryptSM4()
    cipher.set_key(bytes(key), SM4_DECRYPT)
    return bytes(cipher.crypt_cbc(bytes(iv), bytes(ciphertext)))
