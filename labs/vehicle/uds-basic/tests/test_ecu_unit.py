#!/usr/bin/env python3
"""
UDS Basic —— 虚拟 ECU 离线单元测试

本测试直接调用 ecu/ecu.py 中的 handle_uds_request()，
不依赖 vcan0、不依赖 SocketCAN、不依赖 ISO-TP。

因此：

- 可以在任何有 Python 的机器上运行
- 不需要 root 权限
- 不需要 CAN 内核模块
- 不会向任何真实 CAN 总线发送数据

运行：

    python3 tests/test_ecu_unit.py

或：

    bash tests/run_tests.sh
"""

import os
import sys
import unittest

# 让测试可以直接 import ecu/ecu.py 与 ecu/did_database.py
_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

from ecu import (  # noqa: E402
    DEFAULT_SESSION,
    handle_uds_request,
)
from did_database import DID_DATABASE  # noqa: E402


# ----------------------------------------------------------------------
# UDS 常量（ISO 14229-1）
# ----------------------------------------------------------------------

SID_DIAGNOSTIC_SESSION_CONTROL = 0x10
SID_ECU_RESET = 0x11
SID_READ_DATA_BY_IDENTIFIER = 0x22

POS_DIAGNOSTIC_SESSION_CONTROL = 0x50
POS_ECU_RESET = 0x51
POS_READ_DATA_BY_IDENTIFIER = 0x62

NRC_GENERAL_REJECT = 0x10
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_REQUEST_OUT_OF_RANGE = 0x31

DID_F180 = 0xF180
DID_F190 = 0xF190
DID_UNKNOWN = 0xFFFF


def build_negative_response(service: int, nrc: int) -> bytes:
    """构造 UDS 否定响应：7F <SID> <NRC>"""
    return bytes([0x7F, service, nrc])


def parse_negative_response(response: bytes):
    """解析否定响应，返回 (sid, nrc)；不是否定响应则返回 None。"""
    if len(response) == 3 and response[0] == 0x7F:
        return response[1], response[2]
    return None


class UdsTestCase(unittest.TestCase):
    """
    公共基类。

    每个测试开始前重置 ECU 会话状态，保证测试之间互不影响。
    """

    def setUp(self) -> None:
        import ecu

        ecu.CURRENT_SESSION = DEFAULT_SESSION

    # ---- 断言辅助 ----

    def assertPositiveResponse(self, response: bytes, positive_sid: int) -> None:
        self.assertTrue(response, "响应为空")
        self.assertEqual(
            response[0],
            positive_sid,
            f"期望肯定响应 SID 0x{positive_sid:02X}，实际为 {response.hex(' ')}",
        )

    def assertNegativeResponse(self, response: bytes, service: int, nrc: int) -> None:
        parsed = parse_negative_response(response)

        self.assertIsNotNone(
            parsed,
            f"期望否定响应 7F {service:02X} {nrc:02X}，实际为 {response.hex(' ')}",
        )

        actual_service, actual_nrc = parsed

        self.assertEqual(
            actual_service,
            service,
            f"否定响应回显 SID 错误：期望 0x{service:02X}，实际 0x{actual_service:02X}",
        )
        self.assertEqual(
            actual_nrc,
            nrc,
            f"否定响应 NRC 错误：期望 0x{nrc:02X}，实际 0x{actual_nrc:02X}",
        )


# ======================================================================
# 0x10 DiagnosticSessionControl
# ======================================================================


class TestDiagnosticSessionControl(UdsTestCase):
    """0x10 DiagnosticSessionControl 正常与异常路径。"""

    def test_default_session(self) -> None:
        """10 01 -> 50 01 00 32 00 64"""
        response = handle_uds_request(bytes([0x10, 0x01]))

        self.assertEqual(response, bytes([0x50, 0x01, 0x00, 0x32, 0x00, 0x64]))

    def test_programming_session(self) -> None:
        """10 02 -> 50 02 ..."""
        response = handle_uds_request(bytes([0x10, 0x02]))

        self.assertPositiveResponse(response, POS_DIAGNOSTIC_SESSION_CONTROL)
        self.assertEqual(response[1], 0x02)

    def test_extended_diagnostic_session(self) -> None:
        """10 03 -> 50 03 ..."""
        response = handle_uds_request(bytes([0x10, 0x03]))

        self.assertPositiveResponse(response, POS_DIAGNOSTIC_SESSION_CONTROL)
        self.assertEqual(response[1], 0x03)

    def test_session_is_remembered(self) -> None:
        """会话切换后 ECU 内部状态应更新。"""
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))

        self.assertEqual(ecu.CURRENT_SESSION, 0x03)

    def test_unsupported_session_type(self) -> None:
        """10 04 -> 7F 10 12 SubFunctionNotSupported"""
        response = handle_uds_request(bytes([0x10, 0x04]))

        self.assertNegativeResponse(
            response, SID_DIAGNOSTIC_SESSION_CONTROL, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_malformed_request_too_short(self) -> None:
        """10 -> 7F 10 13 IncorrectMessageLengthOrInvalidFormat"""
        response = handle_uds_request(bytes([0x10]))

        self.assertNegativeResponse(
            response, SID_DIAGNOSTIC_SESSION_CONTROL, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_request_too_long(self) -> None:
        """10 01 00 -> 7F 10 13（多余字节）"""
        response = handle_uds_request(bytes([0x10, 0x01, 0x00]))

        self.assertNegativeResponse(
            response, SID_DIAGNOSTIC_SESSION_CONTROL, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_failed_request_does_not_change_session(self) -> None:
        """被拒绝的会话切换不应改变 ECU 状态。"""
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))
        handle_uds_request(bytes([0x10, 0x7F]))

        self.assertEqual(ecu.CURRENT_SESSION, 0x03)


# ======================================================================
# 0x11 ECUReset
# ======================================================================


class TestEcuReset(UdsTestCase):
    """0x11 ECUReset 正常与异常路径。"""

    def test_hard_reset(self) -> None:
        """11 01 -> 51 01"""
        response = handle_uds_request(bytes([0x11, 0x01]))

        self.assertEqual(response, bytes([0x51, 0x01]))

    def test_hard_reset_returns_to_default_session(self) -> None:
        """硬复位后 ECU 必须回到 defaultSession。"""
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))
        self.assertEqual(ecu.CURRENT_SESSION, 0x03)

        handle_uds_request(bytes([0x11, 0x01]))
        self.assertEqual(ecu.CURRENT_SESSION, DEFAULT_SESSION)

    def test_unsupported_reset_type_key_off_on(self) -> None:
        """11 02 -> 7F 11 12（keyOffOnReset 未实现）"""
        response = handle_uds_request(bytes([0x11, 0x02]))

        self.assertNegativeResponse(
            response, SID_ECU_RESET, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_unsupported_reset_type_soft_reset(self) -> None:
        """11 03 -> 7F 11 12（softReset 未实现）"""
        response = handle_uds_request(bytes([0x11, 0x03]))

        self.assertNegativeResponse(
            response, SID_ECU_RESET, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_unsupported_reset_type_unknown_value(self) -> None:
        """11 FF -> 7F 11 12"""
        response = handle_uds_request(bytes([0x11, 0xFF]))

        self.assertNegativeResponse(
            response, SID_ECU_RESET, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_malformed_request_too_short(self) -> None:
        """11 -> 7F 11 13"""
        response = handle_uds_request(bytes([0x11]))

        self.assertNegativeResponse(
            response, SID_ECU_RESET, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_request_too_long(self) -> None:
        """11 01 00 -> 7F 11 13"""
        response = handle_uds_request(bytes([0x11, 0x01, 0x00]))

        self.assertNegativeResponse(
            response, SID_ECU_RESET, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_failed_reset_does_not_change_session(self) -> None:
        """被拒绝的复位不应改变会话状态。"""
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))
        handle_uds_request(bytes([0x11, 0x02]))

        self.assertEqual(ecu.CURRENT_SESSION, 0x03)


# ======================================================================
# 0x22 ReadDataByIdentifier
# ======================================================================


class TestReadDataByIdentifier(UdsTestCase):
    """0x22 ReadDataByIdentifier 正常与异常路径。"""

    def _assert_did_payload(self, response: bytes, did: int) -> bytes:
        """校验响应中第一个 DID 的编码，返回其数据部分。"""
        self.assertPositiveResponse(response, POS_READ_DATA_BY_IDENTIFIER)
        self.assertEqual(
            (response[1] << 8) | response[2],
            did,
            f"响应中的 DID 不是 0x{did:04X}：{response.hex(' ')}",
        )
        return response[3:]

    def test_read_single_did_f190(self) -> None:
        """22 F1 90 -> 62 F1 90 <data>"""
        response = handle_uds_request(bytes([0x22, 0xF1, 0x90]))
        data = self._assert_did_payload(response, DID_F190)

        self.assertEqual(data, DID_DATABASE[DID_F190])

    def test_read_single_did_f180(self) -> None:
        """22 F1 80 -> 62 F1 80 <data>"""
        response = handle_uds_request(bytes([0x22, 0xF1, 0x80]))
        data = self._assert_did_payload(response, DID_F180)

        self.assertEqual(data, DID_DATABASE[DID_F180])

    def test_data_is_ascii(self) -> None:
        """DID 数据应为可读 ASCII（本实验的约定）。"""
        response = handle_uds_request(bytes([0x22, 0xF1, 0x90]))
        data = self._assert_did_payload(response, DID_F190)

        self.assertEqual(data.decode("ascii"), "TESTECU0000000001")

    def test_read_multiple_dids(self) -> None:
        """22 F1 90 F1 80 -> 62 F1 90 <data> F1 80 <data>"""
        response = handle_uds_request(bytes([0x22, 0xF1, 0x90, 0xF1, 0x80]))

        self.assertPositiveResponse(response, POS_READ_DATA_BY_IDENTIFIER)

        did_f190 = DID_DATABASE[DID_F190]
        did_f180 = DID_DATABASE[DID_F180]

        expected = (
            bytes([0x62, 0xF1, 0x90])
            + did_f190
            + bytes([0xF1, 0x80])
            + did_f180
        )

        self.assertEqual(response, expected)

    def test_read_multiple_dids_order_is_preserved(self) -> None:
        """多 DID 响应顺序必须与请求顺序一致。"""
        forward = handle_uds_request(bytes([0x22, 0xF1, 0x80, 0xF1, 0x90]))
        reverse = handle_uds_request(bytes([0x22, 0xF1, 0x90, 0xF1, 0x80]))

        self.assertNotEqual(forward, reverse)

        self.assertEqual((forward[1] << 8) | forward[2], DID_F180)
        self.assertEqual((reverse[1] << 8) | reverse[2], DID_F190)

    def test_unknown_did(self) -> None:
        """22 FF FF -> 7F 22 31 RequestOutOfRange"""
        response = handle_uds_request(bytes([0x22, 0xFF, 0xFF]))

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_REQUEST_OUT_OF_RANGE
        )

    def test_unknown_did_discards_partial_response(self) -> None:
        """一个 DID 未知时，整个请求失败，不得返回部分数据。"""
        response = handle_uds_request(bytes([0x22, 0xF1, 0x90, 0xFF, 0xFF]))

        parsed = parse_negative_response(response)

        self.assertIsNotNone(
            parsed,
            f"期望整体失败（7F 22 31），实际为 {response.hex(' ')}",
        )
        self.assertEqual(parsed[1], NRC_REQUEST_OUT_OF_RANGE)

    def test_malformed_request_too_short(self) -> None:
        """22 -> 7F 22 13"""
        response = handle_uds_request(bytes([0x22]))

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_request_odd_length(self) -> None:
        """22 F1 -> 7F 22 13（DID 不足 2 字节）"""
        response = handle_uds_request(bytes([0x22, 0xF1]))

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_request_odd_length_multi(self) -> None:
        """22 F1 90 F1 -> 7F 22 13"""
        response = handle_uds_request(bytes([0x22, 0xF1, 0x90, 0xF1]))

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_INCORRECT_MESSAGE_LENGTH
        )


# ======================================================================
# 未实现服务 / 通用错误
# ======================================================================


class TestUnsupportedServices(UdsTestCase):
    """未实现服务必须返回 ServiceNotSupported (0x11)。"""

    def test_unimplemented_service_0x27_security_access(self) -> None:
        response = handle_uds_request(bytes([0x27, 0x01]))

        self.assertNegativeResponse(
            response, 0x27, NRC_SERVICE_NOT_SUPPORTED
        )

    def test_unimplemented_service_0x2e_write_data_by_identifier(self) -> None:
        response = handle_uds_request(bytes([0x2E, 0xF1, 0x90, 0x00]))

        self.assertNegativeResponse(
            response, 0x2E, NRC_SERVICE_NOT_SUPPORTED
        )

    def test_unimplemented_service_0x19_read_dtc_information(self) -> None:
        response = handle_uds_request(bytes([0x19, 0x02]))

        self.assertNegativeResponse(
            response, 0x19, NRC_SERVICE_NOT_SUPPORTED
        )

    def test_unimplemented_service_0x3e_tester_present(self) -> None:
        response = handle_uds_request(bytes([0x3E, 0x00]))

        self.assertNegativeResponse(
            response, 0x3E, NRC_SERVICE_NOT_SUPPORTED
        )

    def test_unimplemented_service_0x31_routine_control(self) -> None:
        response = handle_uds_request(bytes([0x31, 0x01]))

        self.assertNegativeResponse(
            response, 0x31, NRC_SERVICE_NOT_SUPPORTED
        )

    def test_negative_response_echoes_requested_sid(self) -> None:
        """否定响应必须回显请求的 SID。"""
        response = handle_uds_request(bytes([0x99]))

        self.assertNegativeResponse(response, 0x99, NRC_SERVICE_NOT_SUPPORTED)

    def test_unknown_service_with_no_data(self) -> None:
        response = handle_uds_request(bytes([0xFF]))

        self.assertNegativeResponse(response, 0xFF, NRC_SERVICE_NOT_SUPPORTED)


class TestMalformedFrames(UdsTestCase):
    """完全无法解析的报文。"""

    def test_empty_request(self) -> None:
        """空请求 -> 7F 00 13"""
        response = handle_uds_request(b"")

        self.assertEqual(
            response, build_negative_response(0x00, NRC_INCORRECT_MESSAGE_LENGTH)
        )

    def test_negative_response_structure_is_always_three_bytes(self) -> None:
        """所有否定响应必须是 7F SID NRC 三个字节。"""
        malformed_requests = [
            b"",
            bytes([0x10]),
            bytes([0x11]),
            bytes([0x22]),
            bytes([0x22, 0xF1]),
            bytes([0x99]),
        ]

        for request in malformed_requests:
            with self.subTest(request=request.hex(" ")):
                response = handle_uds_request(request)

                self.assertEqual(len(response), 3)
                self.assertEqual(response[0], 0x7F)


class TestDidDatabase(UdsTestCase):
    """DID 数据库基本约束。"""

    def test_database_is_not_empty(self) -> None:
        self.assertTrue(DID_DATABASE)

    def test_all_dids_are_two_byte_values(self) -> None:
        for did in DID_DATABASE:
            with self.subTest(did=hex(did)):
                self.assertGreaterEqual(did, 0x0000)
                self.assertLessEqual(did, 0xFFFF)

    def test_all_values_are_bytes(self) -> None:
        for did, value in DID_DATABASE.items():
            with self.subTest(did=hex(did)):
                self.assertIsInstance(value, bytes)
                self.assertGreater(len(value), 0)

    def test_every_did_in_database_is_readable(self) -> None:
        """数据库中每个 DID 都应能通过 0x22 读取。"""
        for did in DID_DATABASE:
            with self.subTest(did=hex(did)):
                response = handle_uds_request(
                    bytes([0x22, (did >> 8) & 0xFF, did & 0xFF])
                )

                self.assertPositiveResponse(
                    response, POS_READ_DATA_BY_IDENTIFIER
                )
                self.assertEqual(response[3:], DID_DATABASE[did])


if __name__ == "__main__":
    unittest.main(verbosity=2)
