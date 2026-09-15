#!/usr/bin/env python3
"""
can-tools 纯逻辑单元测试

本测试只覆盖**不依赖 vcan0 / python-can / python-isotp** 的解析与校验逻辑，
因此可以在任何有 Python 的环境运行：

    python3 tests/test_parsers.py

覆盖：

- CAN ID 解析（0x 前缀 / 十进制 / 边界 / 非法）
- 十六进制字节解析（分隔形式 / 连续形式 / 混合 / 非法）
- 数据长度校验（0 字节 / 8 字节边界 / 超长）
- 接口守卫（只允许 vcan0）
- 帧格式化输出
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _TOOLS_DIR)

from parsers import (  # noqa: E402
    ALLOWED_INTERFACE,
    CAN_DATA_MAX_LEN,
    CAN_ID_MAX,
    CAN_ID_MIN,
    InputError,
    format_can_frame,
    guard_interface,
    parse_can_id,
    parse_hex_bytes,
    validate_can_id,
)


class TestCanIdParsing(unittest.TestCase):
    """CAN ID 解析与校验。"""

    def test_hex_with_prefix(self) -> None:
        self.assertEqual(parse_can_id("0x7E0"), 0x7E0)
        self.assertEqual(parse_can_id("0x7e0"), 0x7E0)
        self.assertEqual(parse_can_id("0X7E8"), 0x7E8)

    def test_decimal(self) -> None:
        self.assertEqual(parse_can_id("2016"), 2016)   # 0x7E0
        self.assertEqual(parse_can_id("2024"), 2024)   # 0x7E8

    def test_whitespace_tolerated(self) -> None:
        self.assertEqual(parse_can_id("  0x7E0  "), 0x7E0)

    def test_boundaries(self) -> None:
        self.assertEqual(parse_can_id("0x000"), CAN_ID_MIN)
        self.assertEqual(parse_can_id("0x7FF"), CAN_ID_MAX)

    def test_rejects_over_max(self) -> None:
        with self.assertRaises(InputError):
            parse_can_id("0x800")

    def test_rejects_negative(self) -> None:
        with self.assertRaises(InputError):
            parse_can_id("-1")

    def test_rejects_non_numeric(self) -> None:
        for bad in ("abc", "0xZZ", "7E0x", "--1"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_can_id(bad)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(InputError):
            parse_can_id("")
        with self.assertRaises(InputError):
            parse_can_id("   ")

    def test_validate_can_id_accepts_int(self) -> None:
        self.assertEqual(validate_can_id(0x7E0), 0x7E0)

    def test_validate_can_id_rejects_bool(self) -> None:
        """bool 是 int 的子类，但不应被当作 CAN ID。"""
        with self.assertRaises(InputError):
            validate_can_id(True)

    def test_validate_can_id_rejects_out_of_range(self) -> None:
        with self.assertRaises(InputError):
            validate_can_id(0x800)
        with self.assertRaises(InputError):
            validate_can_id(-5)


class TestHexByteParsing(unittest.TestCase):
    """十六进制字节解析。"""

    def test_separated_tokens(self) -> None:
        self.assertEqual(parse_hex_bytes(["02", "10", "03"]), bytes([0x02, 0x10, 0x03]))

    def test_contiguous_string(self) -> None:
        self.assertEqual(parse_hex_bytes(["021003"]), bytes([0x02, 0x10, 0x03]))

    def test_mixed_forms(self) -> None:
        self.assertEqual(parse_hex_bytes(["02", "1003"]), bytes([0x02, 0x10, 0x03]))

    def test_common_separators(self) -> None:
        self.assertEqual(parse_hex_bytes(["02:10:03"]), bytes([0x02, 0x10, 0x03]))
        self.assertEqual(parse_hex_bytes(["02,10,03"]), bytes([0x02, 0x10, 0x03]))
        self.assertEqual(parse_hex_bytes(["02-10-03"]), bytes([0x02, 0x10, 0x03]))

    def test_uppercase(self) -> None:
        self.assertEqual(parse_hex_bytes(["AA", "FF"]), bytes([0xAA, 0xFF]))
        self.assertEqual(parse_hex_bytes(["aaff"]), bytes([0xAA, 0xFF]))

    def test_empty_is_allowed_by_parser(self) -> None:
        """空 payload 由 parser 返回 b""；是否允许由各 CLI 决定。"""
        self.assertEqual(parse_hex_bytes([]), b"")
        self.assertEqual(parse_hex_bytes([""]), b"")

    def test_full_eight_bytes(self) -> None:
        data = parse_hex_bytes(["02", "10", "03", "00", "00", "00", "00", "00"])
        self.assertEqual(len(data), CAN_DATA_MAX_LEN)
        self.assertEqual(data, bytes([0x02, 0x10, 0x03, 0, 0, 0, 0, 0]))

    def test_rejects_too_many_bytes(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_hex_bytes(["01"] * 9)
        self.assertIn("超过", str(ctx.exception))

    def test_rejects_odd_length(self) -> None:
        with self.assertRaises(InputError):
            parse_hex_bytes(["021"])

    def test_rejects_invalid_hex(self) -> None:
        for bad in (["zz"], ["0x02"], ["02", "GG"]):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_hex_bytes(bad)

    def test_returns_bytes_type(self) -> None:
        self.assertIsInstance(parse_hex_bytes(["10"]), bytes)


class TestInterfaceGuard(unittest.TestCase):
    """接口守卫：只允许 vcan0。"""

    def test_allows_vcan0(self) -> None:
        self.assertEqual(guard_interface("vcan0"), ALLOWED_INTERFACE)

    def test_allows_vcan0_with_whitespace(self) -> None:
        self.assertEqual(guard_interface("  vcan0 "), ALLOWED_INTERFACE)

    def test_rejects_physical_interfaces(self) -> None:
        for bad in ("can0", "can1", "slcan0", "vcan1", "eth0", "lo"):
            with self.subTest(interface=bad):
                with self.assertRaises(InputError) as ctx:
                    guard_interface(bad)
                self.assertIn("only vcan0", str(ctx.exception))

    def test_rejects_empty(self) -> None:
        with self.assertRaises(InputError):
            guard_interface("")
        with self.assertRaises(InputError):
            guard_interface(None)  # type: ignore[arg-type]

    def test_rejects_case_variant(self) -> None:
        """接口名大小写敏感：VCAN0 不是 vcan0。"""
        with self.assertRaises(InputError):
            guard_interface("VCAN0")


class TestFrameFormatting(unittest.TestCase):
    """帧格式化输出（近似 candump 风格）。"""

    def test_format_with_timestamp(self) -> None:
        line = format_can_frame(1757912345.123456, 0x7E0, bytes([0x02, 0x10, 0x03]))
        self.assertIn("1757912345.123456", line)
        self.assertIn("7E0", line)
        self.assertIn("[3]", line)
        self.assertIn("02 10 03", line)

    def test_format_without_timestamp(self) -> None:
        line = format_can_frame(None, 0x7E8, bytes([0x50]))
        self.assertTrue(line.startswith("-"))
        self.assertIn("7E8", line)

    def test_format_uppercase_hex(self) -> None:
        line = format_can_frame(None, 0x100, bytes([0xAA, 0xBB]))
        self.assertIn("AA BB", line)

    def test_format_empty_data(self) -> None:
        line = format_can_frame(None, 0x000, b"")
        self.assertIn("[0]", line)

    def test_format_id_three_digits(self) -> None:
        line = format_can_frame(None, 0x055, bytes([0x01]))
        self.assertIn("055", line)


class TestToolsAreImportableAndIndependent(unittest.TestCase):
    """三个 CLI 工具的模块可导入，且解析逻辑独立于 CAN 栈。"""

    def test_parsers_has_no_can_dependency(self) -> None:
        """parsers.py 不应 import python-can / python-isotp。"""
        source = open(
            os.path.join(_TOOLS_DIR, "parsers.py"), encoding="utf-8"
        ).read()

        for forbidden in ("import can", "import isotp"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_parsers_does_not_import_can_stack(self) -> None:
        """导入 parsers 后不应把 can / isotp 拉进 sys.modules。"""
        self.assertNotIn("can", sys.modules)
        self.assertNotIn("isotp", sys.modules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
