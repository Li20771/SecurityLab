"""labs/crypto 可复现演示脚本 —— SM2 签名验签 / SM3 摘要 / SM4 加解密。

用法（在 labs/crypto 下）::

    python demo_crypto.py

实测输出见 README.md「实测结果」一节；每次运行的输出保存为
``report/demo_output.txt``（追加模式，带时间戳）。
"""

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from gmcrypto.sm2 import sm2_keygen, sm2_sign, sm2_verify
from gmcrypto.sm3 import sm3_digest, sm3_mac
from gmcrypto.sm4 import (
    sm4_cbc_decrypt,
    sm4_cbc_encrypt,
    sm4_ecb_decrypt,
    sm4_ecb_encrypt,
)

OUT = pathlib.Path(__file__).resolve().parent / "report" / "demo_output.txt"


def main() -> int:
    lines: list[str] = []
    lines.append(f"===== labs/crypto demo @ {datetime.datetime.now().isoformat(timespec='seconds')} =====")

    # 1. SM2 签名 / 验签
    priv, pub = sm2_keygen()
    msg = b"SecurityLab|labs/crypto|demo|message-001"
    sig = sm2_sign(priv, msg)
    ok = sm2_verify(pub, msg, sig)
    tampered = bytearray(msg)
    tampered[0] ^= 0x01
    ok_tamper = sm2_verify(pub, bytes(tampered), sig)
    lines.append("[SM2] private_key (32B): " + priv.hex())
    lines.append("[SM2] public_key  (64B): " + pub.hex())
    lines.append("[SM2] message           : " + msg.decode())
    lines.append("[SM2] signature  (64B)  : " + sig.hex())
    lines.append(f"[SM2] verify(original)  : {ok}")
    lines.append(f"[SM2] verify(tampered)  : {ok_tamper}  (必须 False)")

    # 2. SM3 摘要（字节串 + 文件）
    data = b"SecurityLab SM3 digest demo"
    digest = sm3_digest(data)
    sample_file = pathlib.Path(__file__).resolve().parent / "vectors" / "sm3_kat.json"
    file_digest = sm3_digest(sample_file.read_bytes())
    lines.append(f"[SM3] digest(bytes)     : {digest.hex()}")
    lines.append(f"[SM3] digest(file)      : {file_digest.hex()}  ({sample_file.name})")
    lines.append("[SM3] mac(key‖data)     : " + sm3_mac(b"demo-key-16-bytes", data).hex())

    # 3. SM4 ECB / CBC
    key = bytes(range(16))
    iv = bytes(range(16, 32))
    pt = b"SecurityLab SM4 ECB/CBC demo block 001"
    ct_ecb = sm4_ecb_encrypt(key, pt)
    ct_cbc = sm4_cbc_encrypt(key, iv, pt)
    pt_ecb = sm4_ecb_decrypt(key, ct_ecb)
    pt_cbc = sm4_cbc_decrypt(key, iv, ct_cbc)
    lines.append("[SM4] key               : " + key.hex())
    lines.append("[SM4] iv                : " + iv.hex())
    lines.append("[SM4] plaintext         : " + pt.decode())
    lines.append("[SM4] ECB ciphertext    : " + ct_ecb.hex())
    lines.append("[SM4] CBC ciphertext    : " + ct_cbc.hex())
    lines.append(f"[SM4] ECB roundtrip ok  : {pt_ecb == pt}")
    lines.append(f"[SM4] CBC roundtrip ok  : {pt_cbc == pt}")

    all_ok = ok and not ok_tamper and pt_ecb == pt and pt_cbc == pt
    lines.append(f"===== result: {'PASS' if all_ok else 'FAIL'} =====")

    text = "\n".join(lines)
    print(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
