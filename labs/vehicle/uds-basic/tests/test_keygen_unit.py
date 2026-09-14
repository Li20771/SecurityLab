#!/usr/bin/env python3
"""
keygen.py 独立单元测试

本测试只依赖标准库，**不依赖 CAN / ISO-TP / vcan0**，
因此可在任何机器上运行：

    python3 tests/test_keygen_unit.py

覆盖：

- seed 格式与长度
- seed 的确定性（同输入同输出）与变化性（不同 counter 不同 seed）
- key 的确定性与长度
- key 与 seed 的强相关性
- verify_key 的正确 / 错误 / 边界行为
- 雪崩效应（小的 seed 变化导致 key 大幅变化）
- secret 参与运算
- 非法输入处理
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import keygen  # noqa: E402


class TestSeedGeneration(unittest.TestCase):
    """seed 生成行为。"""

    def test_seed_length_is_four_bytes(self) -> None:
        """本实验约定 seed 为 4 字节。"""
        self.assertEqual(len(keygen.generate_seed(0, 0x01)), 4)

    def test_seed_length_constant_matches(self) -> None:
        self.assertEqual(keygen.generate_seed(0, 0x01), keygen.generate_seed(0, 0x01))
        self.assertEqual(keygen.SEED_LENGTH, 4)

    def test_seed_is_deterministic(self) -> None:
        """纯函数：相同 counter 与 level 必须得到相同 seed。"""
        for counter in range(10):
            with self.subTest(counter=counter):
                self.assertEqual(
                    keygen.generate_seed(counter, 0x01),
                    keygen.generate_seed(counter, 0x01),
                )

    def test_seed_changes_with_counter(self) -> None:
        """连续请求 seed 应该变化。"""
        seeds = [keygen.generate_seed(i, 0x01) for i in range(10)]

        self.assertEqual(
            len(set(seeds)),
            len(seeds),
            f"seed 出现重复：{[s.hex() for s in seeds]}",
        )

    def test_seed_changes_with_level(self) -> None:
        """不同安全等级的 seed 应该不同。"""
        self.assertNotEqual(
            keygen.generate_seed(0, 0x01),
            keygen.generate_seed(0, 0x03),
        )

    def test_seed_is_not_all_zero(self) -> None:
        """
        本实验不使用全 0 seed。

        （ISO 14229 中全 0 seed 表示"已解锁"这一 ISOSAEReserved 语义，
        本实验改用 NRC 0x22 表达该状态，因此 seed 恒非 0。）
        """
        for counter in range(50):
            with self.subTest(counter=counter):
                self.assertNotEqual(keygen.generate_seed(counter, 0x01), b"\x00\x00\x00\x00")

    def test_seed_is_bytes(self) -> None:
        self.assertIsInstance(keygen.generate_seed(0, 0x01), bytes)

    def test_seed_returns_valid_length(self) -> None:
        self.assertTrue(keygen.seed_is_valid(keygen.generate_seed(0, 0x01)))

    def test_seed_is_valid_rejects_wrong_length(self) -> None:
        for bad in (b"", b"\x01", b"\x01\x02\x03", b"\x01\x02\x03\x04\x05"):
            with self.subTest(seed=bad.hex()):
                self.assertFalse(keygen.seed_is_valid(bad))

    def test_seed_is_valid_rejects_non_bytes(self) -> None:
        for bad in (None, 1234, "0102030405", [1, 2, 3, 4]):
            with self.subTest(value=bad):
                self.assertFalse(keygen.seed_is_valid(bad))


class TestKeyDerivation(unittest.TestCase):
    """key 派生行为。"""

    def test_key_length_is_four_bytes(self) -> None:
        seed = keygen.generate_seed(0, 0x01)
        self.assertEqual(len(keygen.derive_key(seed)), 4)

    def test_key_is_deterministic(self) -> None:
        """同 seed 必须得到同 key。"""
        for counter in range(10):
            with self.subTest(counter=counter):
                seed = keygen.generate_seed(counter, 0x01)
                self.assertEqual(keygen.derive_key(seed), keygen.derive_key(seed))

    def test_different_seeds_give_different_keys(self) -> None:
        keys = [keygen.derive_key(keygen.generate_seed(i, 0x01)) for i in range(10)]

        self.assertEqual(len(set(keys)), len(keys), f"key 出现重复：{keys}")

    def test_key_depends_on_seed(self) -> None:
        """seed 变化必须导致 key 变化。"""
        seed_a = bytes([0x10, 0x00, 0x00, 0x00])
        seed_b = bytes([0x10, 0x00, 0x00, 0x01])

        self.assertNotEqual(keygen.derive_key(seed_a), keygen.derive_key(seed_b))

    def test_key_is_not_equal_to_seed(self) -> None:
        """key 不应等于 seed（否则算法无意义）。"""
        for counter in range(10):
            with self.subTest(counter=counter):
                seed = keygen.generate_seed(counter, 0x01)
                self.assertNotEqual(keygen.derive_key(seed), seed)

    def test_key_is_bytes(self) -> None:
        seed = keygen.generate_seed(0, 0x01)
        self.assertIsInstance(keygen.derive_key(seed), bytes)

    def test_derive_key_rejects_wrong_length_seed(self) -> None:
        for bad in (b"", b"\x01\x02\x03", b"\x01\x02\x03\x04\x05"):
            with self.subTest(seed=bad.hex()):
                with self.assertRaises(ValueError):
                    keygen.derive_key(bad)

    def test_derive_key_rejects_non_bytes(self) -> None:
        with self.assertRaises(ValueError):
            keygen.derive_key("01020304")  # type: ignore[arg-type]

    def test_avalanche_effect(self) -> None:
        """
        雪崩效应：seed 变化 1 位，key 应有明显变化。

        教学观察点：不要求严格的 50% 位翻转，只要求变化位数足够多，
        证明确实存在混合运算（而不是简单相加）。
        """
        base_seed = bytes([0x00, 0x00, 0x00, 0x00])
        other_seed = bytes([0x00, 0x00, 0x00, 0x01])

        base_key = int.from_bytes(keygen.derive_key(base_seed), "big")
        other_key = int.from_bytes(keygen.derive_key(other_seed), "big")

        differing_bits = bin(base_key ^ other_key).count("1")

        self.assertGreaterEqual(
            differing_bits,
            8,
            f"key 变化位数过少（{differing_bits} 位），混合强度不足",
        )

    def test_secret_participates_in_derivation(self) -> None:
        """更换 secret 必须得到不同 key（证明 secret 真正参与运算）。"""
        seed = keygen.generate_seed(0, 0x01)

        self.assertNotEqual(
            keygen.derive_key(seed, secret=0x5A17C0DE),
            keygen.derive_key(seed, secret=0x00000000),
        )

    def test_default_secret_is_module_constant(self) -> None:
        seed = keygen.generate_seed(0, 0x01)

        self.assertEqual(
            keygen.derive_key(seed),
            keygen.derive_key(seed, secret=keygen.SECRET),
        )


class TestVerifyKey(unittest.TestCase):
    """verify_key 校验行为。"""

    def setUp(self) -> None:
        self.seed = keygen.generate_seed(0, 0x01)
        self.correct_key = keygen.derive_key(self.seed)

    def test_correct_key_verifies(self) -> None:
        self.assertTrue(keygen.verify_key(self.seed, self.correct_key))

    def test_wrong_key_rejected(self) -> None:
        wrong_key = bytes([self.correct_key[0] ^ 0xFF]) + self.correct_key[1:]
        self.assertFalse(keygen.verify_key(self.seed, wrong_key))

    def test_all_zero_key_rejected(self) -> None:
        self.assertFalse(keygen.verify_key(self.seed, b"\x00\x00\x00\x00"))

    def test_all_ff_key_rejected(self) -> None:
        self.assertFalse(keygen.verify_key(self.seed, b"\xFF\xFF\xFF\xFF"))

    def test_key_for_other_seed_rejected(self) -> None:
        """用另一个 seed 算出的 key 必须被拒绝（防跨 seed 重放）。"""
        other_seed = keygen.generate_seed(1, 0x01)
        other_key = keygen.derive_key(other_seed)

        self.assertNotEqual(other_key, self.correct_key)
        self.assertFalse(keygen.verify_key(self.seed, other_key))

    def test_wrong_key_length_rejected(self) -> None:
        for bad_length in (b"", b"\x01", b"\x01\x02\x03", b"\x01\x02\x03\x04\x05"):
            with self.subTest(length=len(bad_length)):
                self.assertFalse(keygen.verify_key(self.seed, bad_length))

    def test_non_bytes_key_rejected(self) -> None:
        for bad in (None, 1234, "01020304", [1, 2, 3, 4]):
            with self.subTest(value=bad):
                self.assertFalse(keygen.verify_key(self.seed, bad))

    def test_invalid_seed_rejected(self) -> None:
        """seed 非法时不应误判为通过。"""
        self.assertFalse(keygen.verify_key(b"\x01", self.correct_key))
        self.assertFalse(keygen.verify_key(b"", self.correct_key))

    def test_bytearray_accepted(self) -> None:
        """bytearray 形式的 key 应可校验（便于从报文解析）。"""
        self.assertTrue(keygen.verify_key(self.seed, bytearray(self.correct_key)))

    def test_custom_secret_verification(self) -> None:
        custom_secret = 0x12345678
        custom_key = keygen.derive_key(self.seed, secret=custom_secret)

        self.assertTrue(
            keygen.verify_key(self.seed, custom_key, secret=custom_secret)
        )
        self.assertFalse(
            keygen.verify_key(self.seed, custom_key, secret=keygen.SECRET)
        )


class TestKeygenIsPure(unittest.TestCase):
    """keygen 不依赖外部状态 / 依赖。"""

    def test_keygen_does_not_import_can_or_isotp(self) -> None:
        """
        keygen.py 必须不依赖 CAN / ISO-TP。

        这样客户端与测试可以只 import keygen 而不拖入 ISO-TP 依赖。
        """
        source = open(keygen.__file__, "r", encoding="utf-8").read()

        for forbidden in ("import can", "import isotp", "import socket"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    source,
                    f"keygen.py 不应依赖 {forbidden}",
                )

    def test_keygen_importable_without_can_stack(self) -> None:
        """导入 keygen 后不应引入 isotp / can 模块。"""
        self.assertNotIn("isotp", sys.modules)
        self.assertNotIn("can", sys.modules)

    def test_repeated_calls_are_independent(self) -> None:
        """无隐藏状态：调用顺序不影响结果。"""
        seed = keygen.generate_seed(5, 0x01)

        first = keygen.derive_key(seed)
        keygen.generate_seed(99, 0x03)
        keygen.derive_key(keygen.generate_seed(7, 0x01))
        second = keygen.derive_key(seed)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
