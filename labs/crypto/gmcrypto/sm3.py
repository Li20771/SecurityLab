"""SM3 密码杂凑（GB/T 32905-2016）与 SM3-MAC。

算法本体来自 gmssl 3.2.2（纯 Python）。本模块只做接口收敛：

- gmssl 的 ``sm3.sm3_hash`` 进出都是十六进制字符串，这里统一成 bytes 进 / bytes 出
- SM3-MAC 采用最简单的 ``SM3(key ‖ data)`` 构造（教学用，**不是 HMAC-SM3**）；
  只用于完整性演示，不承诺抗长度扩展攻击
"""

from gmssl import func, sm3

__all__ = ["sm3_digest", "sm3_mac"]


def sm3_digest(data: bytes) -> bytes:
    """SM3 摘要（32 字节）。

    >>> sm3_digest(b"abc").hex()
    '66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0'
    """
    return bytes.fromhex(sm3.sm3_hash(func.bytes_to_list(bytes(data))))


def sm3_mac(key: bytes, data: bytes) -> bytes:
    """SM3-MAC（教学用简单构造）：``SM3(key ‖ data)``，32 字节。

    注意：这是前缀 MAC，**不是 HMAC-SM3**，不抗长度扩展攻击；
    仅用于实验室内完整性演示，不可用于生产。
    """
    return sm3_digest(bytes(key) + bytes(data))
