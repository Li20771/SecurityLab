"""gmcrypto —— SM2/SM3/SM4 国密算法的轻量封装（labs/crypto 块 1）。

只做三件事，不做更多：

1. 统一 bytes 进 / bytes 出的接口（gmssl 库内部用 hex/list，这里全部收敛）
2. 补齐 gmssl 缺失的 SM2 密钥对生成（GM/T 0003.5 曲线上的标量乘法）
3. 提供 SM3-MAC（SM3(key‖data)，教学用简单 MAC，非 HMAC）

算法本体全部来自 gmssl 3.2.2，本包只做接口收敛与密钥生成。
"""

from gmcrypto.sm2 import sm2_keygen, sm2_sign, sm2_verify
from gmcrypto.sm3 import sm3_digest, sm3_mac
from gmcrypto.sm4 import (
    sm4_cbc_decrypt,
    sm4_cbc_encrypt,
    sm4_ecb_decrypt,
    sm4_ecb_encrypt,
)

__all__ = [
    "sm2_keygen",
    "sm2_sign",
    "sm2_verify",
    "sm3_digest",
    "sm3_mac",
    "sm4_ecb_encrypt",
    "sm4_ecb_decrypt",
    "sm4_cbc_encrypt",
    "sm4_cbc_decrypt",
]
