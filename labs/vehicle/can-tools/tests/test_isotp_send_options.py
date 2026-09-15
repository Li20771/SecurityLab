#!/usr/bin/env python3
"""
``isotp_send.py`` 显式 ISO-TP 参数的单元测试

覆盖 Phase 4-P2 引入的"显式、可测试、默认行为不变"的传输参数：

- ``--tx-pad`` / ``--rx-pad``      填充字节（单字节校验、None ≠ 0）
- ``--fc-stmin``                   本端流控帧 STmin（ISO-TP 字节 / 时长两种写法）
- ``--fc-blocksize``               本端流控帧块大小 BS
- ``--tx-stmin``                   本端连续帧间隔（纳秒，覆盖收到的 FC STmin）
- 默认行为不变：全部未指定时**不下发任何 socket 选项**

测试分两层：

1. **纯解析层**：不接触 socket，只验证参数字符串 → 数值。
2. **下发层**：用 FakeSocket 记录 set_opts / set_fc_opts 的调用，
   验证"参数名 → ISO-TP 选项"的映射方向正确，且未指定的项不下发。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_isotp_send_options.py

注意：本文件会 import ``isotp_send``（因此需要 python-isotp），
但不打开任何 CAN socket，不需要 vcan0。不需要 python-isotp 的纯解析
测试见 ``test_parsers.py``。
"""

import argparse
import contextlib
import io
import os
import subprocess
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _TOOLS_DIR)

from parsers import (  # noqa: E402
    InputError,
    encode_stmin_ms,
    parse_duration_ns,
    parse_optional_byte,
    parse_stmin_argument,
    stmin_actual_ms,
)
import isotp_send  # noqa: E402


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    """构造一份"全部未指定"的 CLI 参数（argparse.Namespace），再按需覆盖。"""
    values = {
        "tx_pad": None,
        "rx_pad": None,
        "fc_stmin": None,
        "fc_blocksize": None,
        "tx_stmin": None,
    }
    values.update(overrides)

    return argparse.Namespace(**values)


def make_options(**overrides) -> dict:
    """构造一份"全部未指定"的已解析选项 dict，再按需覆盖。"""
    return isotp_send.socket_options(make_args(**overrides))


class FakeSocket:
    """只记录 set_opts / set_fc_opts 调用的假 socket。"""

    def __init__(self):
        self.general_calls = []
        self.flow_control_calls = []

    def set_opts(self, **kwargs):
        self.general_calls.append(kwargs)

    def set_fc_opts(self, **kwargs):
        self.flow_control_calls.append(kwargs)


# ----------------------------------------------------------------------
# 1. 纯解析层
# ----------------------------------------------------------------------


class TestOptionalByteParsing(unittest.TestCase):
    """--tx-pad / --rx-pad / --fc-blocksize 的单字节解析。"""

    def test_unset_is_none(self) -> None:
        """未指定必须是 None，而不是 0（0 是合法取值，不能混为一谈）。"""
        self.assertIsNone(parse_optional_byte(None))
        self.assertIsNone(parse_optional_byte(""))
        self.assertIsNone(parse_optional_byte("   "))

    def test_zero_is_zero_not_none(self) -> None:
        self.assertEqual(parse_optional_byte("0"), 0)
        self.assertEqual(parse_optional_byte("0x00"), 0)

    def test_hex_and_decimal(self) -> None:
        self.assertEqual(parse_optional_byte("0xAA"), 0xAA)
        self.assertEqual(parse_optional_byte("170"), 0xAA)

    def test_boundaries(self) -> None:
        self.assertEqual(parse_optional_byte("0x00"), 0x00)
        self.assertEqual(parse_optional_byte("0xFF"), 0xFF)

    def test_rejects_out_of_range(self) -> None:
        for bad in ("256", "0x100", "-1"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_optional_byte(bad)

    def test_rejects_non_numeric(self) -> None:
        for bad in ("abc", "0xZZ", "none", "null", "default", "1.5"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_optional_byte(bad)


class TestDurationParsing(unittest.TestCase):
    """--tx-stmin 的纳秒解析（内核 CAN_ISOTP_TX_STMIN 的单位）。"""

    def test_unset_is_none(self) -> None:
        self.assertIsNone(parse_duration_ns(None))
        self.assertIsNone(parse_duration_ns(""))

    def test_bare_number_is_nanoseconds(self) -> None:
        self.assertEqual(parse_duration_ns("500"), 500)
        self.assertEqual(parse_duration_ns("0"), 0)

    def test_suffixes(self) -> None:
        self.assertEqual(parse_duration_ns("100us"), 100_000)
        self.assertEqual(parse_duration_ns("2ms"), 2_000_000)
        self.assertEqual(parse_duration_ns("1s"), 1_000_000_000)

    def test_case_and_space_insensitive(self) -> None:
        self.assertEqual(parse_duration_ns("2 MS"), 2_000_000)
        self.assertEqual(parse_duration_ns("2MS"), 2_000_000)
        self.assertEqual(parse_duration_ns("  2ms  "), 2_000_000)

    def test_rejects_negative(self) -> None:
        with self.assertRaises(InputError):
            parse_duration_ns("-1")
        with self.assertRaises(InputError):
            parse_duration_ns("-2ms")

    def test_rejects_overflow(self) -> None:
        """超过 32-bit 无符号上限必须报错（内核字段宽度有限）。"""
        with self.assertRaises(InputError):
            parse_duration_ns("99999999999999")

    def test_rejects_unknown_unit(self) -> None:
        for bad in ("5x", "1min", "s", "abc", "1.5ms"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_duration_ns(bad)

    def test_seconds_unit_kept_distinct_from_milliseconds(self) -> None:
        """单位必须真的生效：2s != 2ms。"""
        self.assertNotEqual(parse_duration_ns("2s"), parse_duration_ns("2ms"))


class TestStminParsing(unittest.TestCase):
    """--fc-stmin：ISO-TP 流控帧 STmin 字节。"""

    def test_unset_is_none(self) -> None:
        self.assertIsNone(parse_stmin_argument(None))
        self.assertIsNone(parse_stmin_argument(""))
        self.assertIsNone(parse_stmin_argument("  "))

    def test_bare_number_is_raw_byte(self) -> None:
        self.assertEqual(parse_stmin_argument("5"), 0x05)
        self.assertEqual(parse_stmin_argument("0"), 0x00)
        self.assertEqual(parse_stmin_argument("127"), 0x7F)

    def test_hex_byte_kept_verbatim(self) -> None:
        """0xF1 是合法微秒档位（0.1 ms），不能被误读成 241 ms。"""
        self.assertEqual(parse_stmin_argument("0xF1"), 0xF1)
        self.assertEqual(parse_stmin_argument("0xF9"), 0xF9)

    def test_millisecond_suffix(self) -> None:
        self.assertEqual(parse_stmin_argument("5ms"), 0x05)
        self.assertEqual(parse_stmin_argument("127ms"), 0x7F)
        self.assertEqual(parse_stmin_argument("0ms"), 0x00)

    def test_microsecond_suffix(self) -> None:
        """500us = 0.5 ms → 0xF5（ISO-TP 微秒档位 0xF1–0xF9）。"""
        self.assertEqual(parse_stmin_argument("500us"), 0xF5)
        self.assertEqual(parse_stmin_argument("100us"), 0xF1)
        self.assertEqual(parse_stmin_argument("900us"), 0xF9)

    def test_sub_millisecond_decimal(self) -> None:
        self.assertEqual(parse_stmin_argument("0.5ms"), 0xF5)
        self.assertEqual(parse_stmin_argument("0.1ms"), 0xF1)

    def test_stmin_round_trip_through_decoder(self) -> None:
        """编码结果必须能被解码回接近原值的毫秒数。"""
        for text, expected_ms in (
            ("0", 0.0),
            ("5", 5.0),
            ("127", 127.0),
            ("5ms", 5.0),
            ("500us", 0.5),
            ("0xF1", 0.1),
            ("0xF9", 0.9),
        ):
            with self.subTest(value=text):
                byte = parse_stmin_argument(text)
                self.assertAlmostEqual(stmin_actual_ms(byte), expected_ms, places=6)

    def test_rejects_out_of_range_ms(self) -> None:
        """超过 127 ms 无法用 STmin 字节表示，必须报错而不是悄悄取别的值。"""
        for bad in ("128ms", "1000ms", "1s"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_stmin_argument(bad)

    def test_rejects_out_of_byte_range(self) -> None:
        for bad in ("256", "0x100", "-1"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_stmin_argument(bad)

    def test_rejects_non_numeric(self) -> None:
        for bad in ("abc", "none", "null", "5x"):
            with self.subTest(value=bad):
                with self.assertRaises(InputError):
                    parse_stmin_argument(bad)


class TestStminEncoding(unittest.TestCase):
    """encode_stmin_ms：毫秒 → STmin 字节。"""

    def test_millisecond_band(self) -> None:
        self.assertEqual(encode_stmin_ms(0), 0x00)
        self.assertEqual(encode_stmin_ms(1), 0x01)
        self.assertEqual(encode_stmin_ms(5), 0x05)
        self.assertEqual(encode_stmin_ms(127), 0x7F)

    def test_microsecond_band(self) -> None:
        self.assertEqual(encode_stmin_ms(0.1), 0xF1)
        self.assertEqual(encode_stmin_ms(0.5), 0xF5)
        self.assertEqual(encode_stmin_ms(0.9), 0xF9)

    def test_rejects_negative_and_too_large(self) -> None:
        with self.assertRaises(InputError):
            encode_stmin_ms(-1)
        with self.assertRaises(InputError):
            encode_stmin_ms(127.5)
        with self.assertRaises(InputError):
            encode_stmin_ms(128)


# ----------------------------------------------------------------------
# 2. 下发层（FakeSocket）
# ----------------------------------------------------------------------


class TestSocketOptionsResolver(unittest.TestCase):
    """socket_options()：只保留显式指定的项。"""

    def test_all_unset_yields_all_none(self) -> None:
        options = isotp_send.socket_options(make_args())
        self.assertEqual(
            options,
            {
                "tx_pad": None,
                "rx_pad": None,
                "fc_stmin": None,
                "fc_blocksize": None,
                "tx_stmin": None,
            },
        )

    def test_values_pass_through(self) -> None:
        options = isotp_send.socket_options(
            make_args(
                tx_pad=0xAA,
                rx_pad=0xCC,
                fc_stmin=0x05,
                fc_blocksize=1,
                tx_stmin=2_000_000,
            )
        )
        self.assertEqual(options["tx_pad"], 0xAA)
        self.assertEqual(options["rx_pad"], 0xCC)
        self.assertEqual(options["fc_stmin"], 0x05)
        self.assertEqual(options["fc_blocksize"], 1)
        self.assertEqual(options["tx_stmin"], 2_000_000)


class TestApplySocketOptions(unittest.TestCase):
    """apply_socket_options()：参数 → ISO-TP 选项的映射方向。"""

    def test_defaults_issue_no_syscall(self) -> None:
        """
        关键回归点：全部未指定时**不能**调用 set_opts / set_fc_opts。

        一旦调用，就会把内核默认行为改掉，因此这里断言"零调用"。
        """
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options())

        self.assertEqual(sock.general_calls, [])
        self.assertEqual(sock.flow_control_calls, [])

    def test_tx_pad_maps_to_txpad(self) -> None:
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options(tx_pad=0xAA))

        self.assertEqual(sock.general_calls, [{"txpad": 0xAA}])
        self.assertEqual(sock.flow_control_calls, [])

    def test_rx_pad_maps_to_rxpad(self) -> None:
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options(rx_pad=0xCC))

        self.assertEqual(sock.general_calls, [{"rxpad": 0xCC}])

    def test_tx_stmin_maps_to_general_tx_stmin(self) -> None:
        """--tx-stmin 走 set_opts(tx_stmin=...)，单位纳秒。"""
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options(tx_stmin=2_000_000))

        self.assertEqual(sock.general_calls, [{"tx_stmin": 2_000_000}])
        self.assertEqual(sock.flow_control_calls, [])

    def test_fc_stmin_maps_to_flow_control_stmin(self) -> None:
        """--fc-stmin 走 set_fc_opts(stmin=...)，是 FC 字节。"""
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options(fc_stmin=0x05))

        self.assertEqual(sock.general_calls, [])
        self.assertEqual(sock.flow_control_calls, [{"stmin": 0x05}])

    def test_fc_blocksize_maps_to_flow_control_bs(self) -> None:
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options(fc_blocksize=1))

        self.assertEqual(sock.flow_control_calls, [{"bs": 1}])

    def test_fc_stmin_and_blocksize_combined(self) -> None:
        sock = FakeSocket()

        isotp_send.apply_socket_options(
            sock, make_options(fc_stmin=0x05, fc_blocksize=2)
        )

        self.assertEqual(len(sock.flow_control_calls), 1)
        self.assertEqual(sock.flow_control_calls[0], {"bs": 2, "stmin": 0x05})

    def test_tx_and_rx_pad_combined_into_one_call(self) -> None:
        sock = FakeSocket()

        isotp_send.apply_socket_options(sock, make_options(tx_pad=0xAA, rx_pad=0xCC))

        self.assertEqual(len(sock.general_calls), 1)
        self.assertEqual(sock.general_calls[0], {"txpad": 0xAA, "rxpad": 0xCC})

    def test_zero_values_are_still_applied(self) -> None:
        """0 是显式取值，必须下发（与 None 严格区分）。"""
        sock = FakeSocket()

        isotp_send.apply_socket_options(
            sock, make_options(fc_stmin=0x00, fc_blocksize=0, tx_stmin=0)
        )

        self.assertEqual(sock.general_calls, [{"tx_stmin": 0}])
        self.assertEqual(sock.flow_control_calls, [{"bs": 0, "stmin": 0x00}])

    def test_two_directions_stay_separate(self) -> None:
        """
        --fc-stmin 与 --tx-stmin 方向不同，不能落到同一个选项上。

        这是本项目最重要的传输层概念区分：
          fc_stmin → 我们发出的 FC 里的字节（要求对方放慢）
          tx_stmin → 我们自己发 CF 的纳秒间隔（我们自己放慢）
        """
        sock = FakeSocket()

        isotp_send.apply_socket_options(
            sock, make_options(fc_stmin=0x05, tx_stmin=5_000_000)
        )

        self.assertEqual(sock.general_calls, [{"tx_stmin": 5_000_000}])
        self.assertEqual(sock.flow_control_calls, [{"stmin": 0x05}])


class TestDescribeOptional(unittest.TestCase):
    """显示层必须让"未指定"和 0 看起来不同。"""

    def test_none_is_labelled(self) -> None:
        self.assertIn("未指定", isotp_send._describe_optional(None))

    def test_zero_is_hex_not_unset(self) -> None:
        text = isotp_send._describe_optional(0)
        self.assertEqual(text, "0x00")
        self.assertNotIn("未指定", text)


class TestParserWiring(unittest.TestCase):
    """CLI 参数表接线：名字、默认值、解析函数。"""

    def setUp(self) -> None:
        self.parser = isotp_send.build_parser()

    def test_defaults_are_unset(self) -> None:
        args = self.parser.parse_args(["10", "03"])

        self.assertIsNone(args.tx_pad)
        self.assertIsNone(args.rx_pad)
        self.assertIsNone(args.fc_stmin)
        self.assertIsNone(args.fc_blocksize)
        self.assertIsNone(args.tx_stmin)

    def test_timeout_default_preserved(self) -> None:
        """--timeout 默认值必须仍等于既有 client 的 2.0。"""
        args = self.parser.parse_args(["10", "03"])

        self.assertEqual(args.timeout, isotp_send.DEFAULT_TIMEOUT)
        self.assertEqual(isotp_send.DEFAULT_TIMEOUT, 2.0)

    def test_all_options_parsed(self) -> None:
        args = self.parser.parse_args(
            [
                "--tx-pad", "0xAA",
                "--rx-pad", "0xCC",
                "--fc-stmin", "5ms",
                "--fc-blocksize", "1",
                "--tx-stmin", "2ms",
                "22", "F1", "90",
            ]
        )

        self.assertEqual(args.tx_pad, 0xAA)
        self.assertEqual(args.rx_pad, 0xCC)
        self.assertEqual(args.fc_stmin, 0x05)
        self.assertEqual(args.fc_blocksize, 1)
        self.assertEqual(args.tx_stmin, 2_000_000)
        self.assertEqual(args.payload, ["22", "F1", "90"])

    def test_invalid_option_exits_with_code_2(self) -> None:
        """非法参数必须立刻失败（退出码 2），不能静默回退到默认值。"""
        for argv in (
            ["--tx-pad", "zz", "10", "03"],
            ["--tx-pad", "256", "10", "03"],
            ["--fc-stmin", "128ms", "10", "03"],
            ["--tx-stmin", "abc", "10", "03"],
            ["--fc-blocksize", "-1", "10", "03"],
        ):
            with self.subTest(argv=argv):
                # argparse 会把 usage + error 写到 stderr，这里吞掉避免测试输出噪音
                with self.assertRaises(SystemExit) as ctx:
                    with contextlib.redirect_stderr(io.StringIO()):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.parser.parse_args(argv)

                self.assertEqual(ctx.exception.code, 2)

    def test_invalid_option_reports_clear_message(self) -> None:
        """
        报错信息必须能让使用者看懂是哪个参数、哪个值有问题。

        回归点：早期版本用 lambda 做 ``type=``，argparse 会打出
        ``invalid <lambda> value``，完全看不出是哪个选项。
        """
        stderr = io.StringIO()

        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(stderr):
                self.parser.parse_args(["--fc-stmin", "128ms", "10", "03"])

        message = stderr.getvalue()

        self.assertIn("--fc-stmin", message)
        self.assertIn("128ms", message)
        self.assertNotIn("<lambda>", message)

    def test_interface_still_guarded(self) -> None:
        """P2 不应放松接口守卫：--interface 仍只接受 vcan0。"""
        args = self.parser.parse_args(["--interface", "can0", "10", "03"])

        with self.assertRaises(InputError):
            isotp_send.guard_interface(args.interface)


class TestCliTimeoutWithoutEcu(unittest.TestCase):
    """G. --timeout：没有 ECU 应答时必须**干净退出**，不能挂死。

    这是 P2 显式化超时参数的直接可验证行为：
    默认 2.0s，显式指定 0.5s 时应约 0.5s 返回并给出超时提示。
    """

    def setUp(self) -> None:
        if not os.path.exists(f"/sys/class/net/{isotp_send.ALLOWED_INTERFACE}"):
            self.skipTest(f"{isotp_send.ALLOWED_INTERFACE} 不存在")

    def _run_tool(self, timeout_value: str, extra=None):
        argv = [
            sys.executable,
            os.path.join(_TOOLS_DIR, "isotp_send.py"),
            "--timeout", timeout_value,
            "22", "F1", "90",
        ]

        if extra:
            argv[1:1] = extra

        started = time.monotonic()
        result = subprocess.run(
            argv,
            cwd=_TOOLS_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )

        return result, time.monotonic() - started

    def test_short_timeout_exits_cleanly(self) -> None:
        result, elapsed = self._run_tool("0.5")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("timeout", result.stdout.lower())
        # 0.5s 超时不应变成长时间阻塞（留足脚本启动余量）
        self.assertLess(elapsed, 10.0, f"耗时 {elapsed:.1f}s，超时参数似乎没生效")

    def test_no_ecu_still_binds_and_sends(self) -> None:
        """即使没有 ECU，工具也必须成功 bind 并把 payload 发出去。"""
        result, _ = self._run_tool("0.5")

        self.assertIn("TX: 22 f1 90", result.stdout)
        self.assertNotIn("无法在 vcan0 上绑定", result.stderr)

    def test_invalid_timeout_value_rejected(self) -> None:
        """非法 --timeout 必须报错退出（不能被悄悄修正）。"""
        argv = [
            sys.executable,
            os.path.join(_TOOLS_DIR, "isotp_send.py"),
            "--timeout", "abc",
            "22", "F1", "90",
        ]

        result = subprocess.run(
            argv, cwd=_TOOLS_DIR, capture_output=True, text=True, timeout=30
        )

        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
