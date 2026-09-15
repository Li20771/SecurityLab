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
import time
import unittest
from unittest import mock

# 让测试可以直接 import ecu/ecu.py 与 ecu/did_database.py
_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import ecu as ecu_module  # noqa: E402
import keygen  # noqa: E402
from ecu import (  # noqa: E402
    DEFAULT_SESSION,
    handle_uds_request,
)
from did_database import DID_DATABASE, PROTECTED_DIDS  # noqa: E402


# ----------------------------------------------------------------------
# UDS 常量（ISO 14229-1）
# ----------------------------------------------------------------------

SID_DIAGNOSTIC_SESSION_CONTROL = 0x10
SID_ECU_RESET = 0x11
SID_READ_DATA_BY_IDENTIFIER = 0x22
SID_SECURITY_ACCESS = 0x27

POS_DIAGNOSTIC_SESSION_CONTROL = 0x50
POS_ECU_RESET = 0x51
POS_READ_DATA_BY_IDENTIFIER = 0x62
POS_SECURITY_ACCESS = 0x67

NRC_GENERAL_REJECT = 0x10
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_INVALID_KEY = 0x35
NRC_EXCEED_NUMBER_OF_ATTEMPTS = 0x36
NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED = 0x37

SUBFUNCTION_REQUEST_SEED = 0x01
SUBFUNCTION_SEND_KEY = 0x02

DID_F180 = 0xF180
DID_F190 = 0xF190
DID_F187_PROTECTED = 0xF187
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

    每个测试开始前重置 ECU 全部可变状态（会话 + 安全访问），
    保证测试之间完全互不影响。
    """

    def setUp(self) -> None:
        import ecu

        ecu.STATE.session = DEFAULT_SESSION
        ecu.reset_all_security_state()

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

        self.assertEqual(ecu.STATE.session, 0x03)

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

        self.assertEqual(ecu.STATE.session, 0x03)


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
        self.assertEqual(ecu.STATE.session, 0x03)

        handle_uds_request(bytes([0x11, 0x01]))
        self.assertEqual(ecu.STATE.session, DEFAULT_SESSION)

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

        self.assertEqual(ecu.STATE.session, 0x03)


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

    def test_unimplemented_service_0x2e_write_data_by_identifier(self) -> None:
        response = handle_uds_request(bytes([0x2E, 0xF1, 0x90, 0x00]))

        self.assertNegativeResponse(
            response, 0x2E, NRC_SERVICE_NOT_SUPPORTED
        )

    def test_security_access_is_implemented(self) -> None:
        """
        0x27 已实现：RequestSeed 应返回 67 01 <4 字节 seed>。

        （本用例原为 test_unimplemented_service_0x27_security_access，
        断言 7F 27 11；SecurityAccess 实现后改写为断言新行为。）
        """
        response = handle_uds_request(bytes([SID_SECURITY_ACCESS, SUBFUNCTION_REQUEST_SEED]))

        self.assertPositiveResponse(response, POS_SECURITY_ACCESS)
        self.assertEqual(response[1], SUBFUNCTION_REQUEST_SEED)
        self.assertEqual(len(response), 2 + 4)

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

    def test_every_public_did_in_database_is_readable(self) -> None:
        """数据库中的公开 DID 都应能直接通过 0x22 读取。"""
        for did in DID_DATABASE:
            if did in PROTECTED_DIDS:
                continue

            with self.subTest(did=hex(did)):
                response = handle_uds_request(
                    bytes([0x22, (did >> 8) & 0xFF, did & 0xFF])
                )

                self.assertPositiveResponse(
                    response, POS_READ_DATA_BY_IDENTIFIER
                )
                self.assertEqual(response[3:], DID_DATABASE[did])

    def test_protected_dids_are_subset_of_database(self) -> None:
        """PROTECTED_DIDS 中的每个 DID 都必须真实存在于数据库。"""
        for did in PROTECTED_DIDS:
            with self.subTest(did=hex(did)):
                self.assertIn(did, DID_DATABASE)

    def test_protected_dids_are_not_empty(self) -> None:
        self.assertTrue(PROTECTED_DIDS, "本实验应至少有一个受保护 DID")


# ======================================================================
# 0x27 SecurityAccess
# ======================================================================


class SecurityAccessTestCase(UdsTestCase):
    """SecurityAccess 测试公共工具。"""

    def request_seed(self, subfunction: int = SUBFUNCTION_REQUEST_SEED) -> bytes:
        return handle_uds_request(bytes([SID_SECURITY_ACCESS, subfunction]))

    def send_key(self, key: bytes, subfunction: int = SUBFUNCTION_SEND_KEY) -> bytes:
        return handle_uds_request(
            bytes([SID_SECURITY_ACCESS, subfunction]) + bytes(key)
        )

    def get_seed(self) -> bytes:
        """请求 seed 并返回 seed 字节（断言响应格式正确）。"""
        response = self.request_seed()

        self.assertPositiveResponse(response, POS_SECURITY_ACCESS)
        self.assertEqual(response[1], SUBFUNCTION_REQUEST_SEED)
        self.assertEqual(len(response), 2 + keygen.SEED_LENGTH)

        return response[2:]

    def unlock(self) -> bytes:
        """走完整 seed/key 流程完成解锁，返回使用的 seed。"""
        seed = self.get_seed()

        response = self.send_key(keygen.derive_key(seed))

        self.assertPositiveResponse(response, POS_SECURITY_ACCESS)
        self.assertEqual(response[1], SUBFUNCTION_SEND_KEY)

        return seed

    def wrong_key_for(self, seed: bytes) -> bytes:
        """由正确 key 翻转全部位，得到稳定可复现的错误 key。"""
        correct = keygen.derive_key(seed)

        return bytes(byte ^ 0xFF for byte in correct)


class TestSecurityAccessRequestSeed(SecurityAccessTestCase):
    """27 01 RequestSeed。"""

    def test_request_seed_returns_positive_response(self) -> None:
        """27 01 -> 67 01 <4 字节 seed>"""
        response = self.request_seed()

        self.assertPositiveResponse(response, POS_SECURITY_ACCESS)
        self.assertEqual(response[1], SUBFUNCTION_REQUEST_SEED)

    def test_request_seed_payload_length(self) -> None:
        """响应长度固定为 2 + 4 = 6 字节（可装入 ISO-TP 单帧）。"""
        response = self.request_seed()

        self.assertEqual(len(response), 6)

    def test_request_seed_is_four_bytes(self) -> None:
        seed = self.get_seed()

        self.assertEqual(len(seed), keygen.SEED_LENGTH)

    def test_request_seed_is_not_all_zero(self) -> None:
        """
        本实验不使用全 0 seed。

        ISO 14229 中全 0 seed 表示"已解锁"这一 ISOSAEReserved 语义，
        本实验改用 NRC 0x22 表达该状态。
        """
        seed = self.get_seed()

        self.assertNotEqual(seed, b"\x00\x00\x00\x00")

    def test_seed_changes_between_requests(self) -> None:
        """连续请求 seed 应该得到不同 seed。"""
        first = self.get_seed()
        second = self.get_seed()

        self.assertNotEqual(first, second, "seed 在两次请求间没有变化")

    def test_seed_stored_as_pending(self) -> None:
        import ecu

        seed = self.get_seed()

        self.assertEqual(ecu.STATE.pending_seed, seed)
        self.assertIsNotNone(ecu.STATE.seed_issued_at)
        self.assertFalse(ecu.STATE.seed_consumed)

    def test_seed_counter_increments(self) -> None:
        import ecu

        self.get_seed()
        first_counter = ecu.STATE.seed_counter

        self.get_seed()

        self.assertEqual(ecu.STATE.seed_counter, first_counter + 1)

    def test_second_request_invalidates_old_seed(self) -> None:
        """重新请求 seed 后，旧 seed 对应的 key 必须失效。"""
        old_seed = self.get_seed()
        old_key = keygen.derive_key(old_seed)

        self.get_seed()

        response = self.send_key(old_key)

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INVALID_KEY
        )

    def test_request_seed_while_unlocked_returns_conditions_not_correct(self) -> None:
        """已解锁时再次 RequestSeed -> 7F 27 22。"""
        self.unlock()

        response = self.request_seed()

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_CONDITIONS_NOT_CORRECT
        )


class TestSecurityAccessSendKey(SecurityAccessTestCase):
    """27 02 SendKey。"""

    def test_correct_key_unlocks(self) -> None:
        """27 02 <正确 key> -> 67 02"""
        seed = self.get_seed()

        response = self.send_key(keygen.derive_key(seed))

        self.assertEqual(response, bytes([0x67, 0x02]))

    def test_correct_key_sets_security_level(self) -> None:
        import ecu

        self.unlock()

        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LEVEL1)

    def test_correct_key_clears_pending_seed(self) -> None:
        import ecu

        self.unlock()

        self.assertIsNone(ecu.STATE.pending_seed)
        self.assertTrue(ecu.STATE.seed_consumed)

    def test_correct_key_resets_failed_attempts(self) -> None:
        import ecu

        seed = self.get_seed()
        self.send_key(self.wrong_key_for(seed))
        self.assertGreater(ecu.STATE.failed_attempts, 0)

        self.get_seed()
        seed = ecu.STATE.pending_seed
        self.send_key(keygen.derive_key(seed))

        self.assertEqual(ecu.STATE.failed_attempts, 0)

    def test_wrong_key_returns_invalid_key(self) -> None:
        """错误 key -> 7F 27 35"""
        seed = self.get_seed()

        response = self.send_key(self.wrong_key_for(seed))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INVALID_KEY
        )

    def test_wrong_key_does_not_unlock(self) -> None:
        import ecu

        seed = self.get_seed()
        self.send_key(self.wrong_key_for(seed))

        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LOCKED)

    def test_wrong_key_increments_failed_attempts(self) -> None:
        import ecu

        self.assertEqual(ecu.STATE.failed_attempts, 0)

        seed = self.get_seed()
        self.send_key(self.wrong_key_for(seed))

        self.assertEqual(ecu.STATE.failed_attempts, 1)

    def test_all_zero_key_rejected(self) -> None:
        self.get_seed()

        response = self.send_key(bytes(4))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INVALID_KEY
        )

    def test_key_from_other_seed_rejected(self) -> None:
        """用另一个 seed 算出的 key 必须被拒绝。"""
        self.get_seed()
        other_key = keygen.derive_key(keygen.generate_seed(999, 0x01))

        response = self.send_key(other_key)

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INVALID_KEY
        )


class TestSecurityAccessSequence(SecurityAccessTestCase):
    """请求顺序错误 -> 7F 27 24。"""

    def test_send_key_without_seed(self) -> None:
        """没有 seed 直接发送 key -> 7F 27 24"""
        response = self.send_key(bytes(4))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_REQUEST_SEQUENCE_ERROR
        )

    def test_replay_consumed_seed(self) -> None:
        """已成功消费的 seed 再次使用 -> 7F 27 24（防重放）。"""
        seed = self.get_seed()
        key = keygen.derive_key(seed)

        first = self.send_key(key)
        self.assertEqual(first, bytes([0x67, 0x02]))

        replay = self.send_key(key)

        self.assertNegativeResponse(
            replay, SID_SECURITY_ACCESS, NRC_REQUEST_SEQUENCE_ERROR
        )

    def test_expired_seed_returns_sequence_error(self) -> None:
        """seed 超时后发送正确 key -> 7F 27 24（注入时钟，不 sleep）。"""
        seed = self.get_seed()

        with mock.patch.object(
            ecu_module,
            "_now",
            return_value=time.monotonic() + ecu_module.SECURITY_CONFIG["seed_expiry_seconds"] + 1,
        ):
            response = self.send_key(keygen.derive_key(seed))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_REQUEST_SEQUENCE_ERROR
        )

    def test_seed_valid_just_before_expiry(self) -> None:
        """seed 在到期前 1 秒仍然有效（边界验证）。"""
        seed = self.get_seed()

        issued_at = ecu_module.STATE.seed_issued_at
        almost_expired = (
            issued_at + ecu_module.SECURITY_CONFIG["seed_expiry_seconds"] - 1
        )

        with mock.patch.object(ecu_module, "_now", return_value=almost_expired):
            response = self.send_key(keygen.derive_key(seed))

        self.assertEqual(response, bytes([0x67, 0x02]))


class TestSecurityAccessAttemptLimit(SecurityAccessTestCase):
    """失败次数与锁定。"""

    def test_attempt_limit_sequence_is_35_35_36(self) -> None:
        """三次错误 key -> 35, 35, 36（0x36 只出现在触发锁定的那一次）。"""
        expected = [NRC_INVALID_KEY, NRC_INVALID_KEY, NRC_EXCEED_NUMBER_OF_ATTEMPTS]
        actual = []

        for _ in range(3):
            seed = self.get_seed()
            response = self.send_key(self.wrong_key_for(seed))
            actual.append(response[2])

        self.assertEqual(actual, expected)

    def test_lockout_until_is_set_on_limit(self) -> None:
        import ecu

        self.assertIsNone(ecu.STATE.lockout_until)

        for _ in range(ecu.SECURITY_CONFIG["max_attempts"]):
            seed = self.get_seed()
            self.send_key(self.wrong_key_for(seed))

        self.assertIsNotNone(ecu.STATE.lockout_until)
        self.assertEqual(
            ecu.STATE.failed_attempts, ecu.SECURITY_CONFIG["max_attempts"]
        )

    def test_any_0x27_during_lockout_returns_37(self) -> None:
        """锁定期间任意 0x27 请求（含 RequestSeed）-> 7F 27 37。"""
        self._trigger_lockout()

        self.assertNegativeResponse(
            self.request_seed(),
            SID_SECURITY_ACCESS,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
        )
        self.assertNegativeResponse(
            self.send_key(bytes(4)),
            SID_SECURITY_ACCESS,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
        )

    def test_correct_key_during_lockout_still_returns_37(self) -> None:
        """锁定期间即使 key 正确也必须被拒绝。"""
        self._trigger_lockout()

        # 锁定后 PENDING_SEED 已被清除，这里构造任意 key
        response = self.send_key(bytes(4))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED
        )

    def test_unsupported_subfunction_during_lockout(self) -> None:
        """
        锁定检查位于子功能与长度检查之后：

        27 03 在锁定期间仍返回 0x12（子功能不支持），
        而不是 0x37。这符合本项目约定的 NRC 优先级。
        """
        self._trigger_lockout()

        response = handle_uds_request(bytes([SID_SECURITY_ACCESS, 0x03]))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_lockout_expires_allows_retry(self) -> None:
        """锁定到期后允许重新 RequestSeed，且失败计数清零。"""
        self._trigger_lockout()

        expired = (
            ecu_module.STATE.lockout_until
            + ecu_module.SECURITY_CONFIG["lockout_seconds"]
            + 1
        )

        with mock.patch.object(ecu_module, "_now", return_value=expired):
            response = self.request_seed()

        self.assertPositiveResponse(response, POS_SECURITY_ACCESS)
        self.assertIsNone(ecu_module.STATE.lockout_until)
        self.assertEqual(ecu_module.STATE.failed_attempts, 0)

    def test_failed_attempts_reset_after_lockout(self) -> None:
        import ecu

        self._trigger_lockout()

        expired = (
            ecu.STATE.lockout_until + ecu.SECURITY_CONFIG["lockout_seconds"] + 1
        )

        with mock.patch.object(ecu, "_now", return_value=expired):
            self.request_seed()

        self.assertEqual(ecu.STATE.failed_attempts, 0)

    def _trigger_lockout(self) -> None:
        """连续用错误 key 触发锁定。"""
        import ecu

        for _ in range(ecu.SECURITY_CONFIG["max_attempts"]):
            seed = self.get_seed()
            self.send_key(self.wrong_key_for(seed))


class TestSecurityAccessValidation(SecurityAccessTestCase):
    """子功能与长度校验。"""

    def test_unsupported_subfunction_0x03(self) -> None:
        response = handle_uds_request(bytes([SID_SECURITY_ACCESS, 0x03]))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_unsupported_subfunction_0x04(self) -> None:
        response = handle_uds_request(bytes([SID_SECURITY_ACCESS, 0x04]))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_unsupported_subfunction_0x05(self) -> None:
        response = handle_uds_request(bytes([SID_SECURITY_ACCESS, 0x05]))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_unsupported_subfunction_ff(self) -> None:
        response = handle_uds_request(bytes([SID_SECURITY_ACCESS, 0xFF]))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_malformed_no_subfunction(self) -> None:
        """27 -> 7F 27 13"""
        response = handle_uds_request(bytes([SID_SECURITY_ACCESS]))

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_send_key_without_key(self) -> None:
        """27 02 -> 7F 27 13"""
        response = handle_uds_request(
            bytes([SID_SECURITY_ACCESS, SUBFUNCTION_SEND_KEY])
        )

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_send_key_too_short(self) -> None:
        """27 02 <3 字节> -> 7F 27 13"""
        response = handle_uds_request(
            bytes([SID_SECURITY_ACCESS, SUBFUNCTION_SEND_KEY, 0x01, 0x02, 0x03])
        )

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_send_key_too_long(self) -> None:
        """27 02 <5 字节> -> 7F 27 13"""
        response = handle_uds_request(
            bytes([SID_SECURITY_ACCESS, SUBFUNCTION_SEND_KEY])
            + bytes([0x01, 0x02, 0x03, 0x04, 0x05])
        )

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_malformed_request_seed_with_extra_bytes(self) -> None:
        """27 01 00 -> 7F 27 13"""
        response = handle_uds_request(
            bytes([SID_SECURITY_ACCESS, SUBFUNCTION_REQUEST_SEED, 0x00])
        )

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH
        )

    def test_nrc_priority_subfunction_before_length(self) -> None:
        """
        NRC 优先级：子功能检查先于长度检查。

        27 03 <多余字节> 应返回 0x12（子功能不支持），而不是 0x13。
        """
        response = handle_uds_request(
            bytes([SID_SECURITY_ACCESS, 0x03, 0x00, 0x00])
        )

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED
        )

    def test_negative_responses_are_three_bytes(self) -> None:
        """所有 0x27 否定响应恒为 3 字节。"""
        requests = [
            bytes([SID_SECURITY_ACCESS]),
            bytes([SID_SECURITY_ACCESS, 0x02]),
            bytes([SID_SECURITY_ACCESS, 0x03]),
            bytes([SID_SECURITY_ACCESS, 0x02]) + bytes(4),
            bytes([SID_SECURITY_ACCESS, 0x01, 0x00]),
        ]

        for request in requests:
            with self.subTest(request=request.hex(" ")):
                response = handle_uds_request(request)

                self.assertEqual(len(response), 3)
                self.assertEqual(response[0], 0x7F)
                self.assertEqual(response[1], SID_SECURITY_ACCESS)


class TestSecurityStateTransitions(SecurityAccessTestCase):
    """会话切换与 ECUReset 对安全状态的影响。"""

    def test_session_switch_to_default_locks(self) -> None:
        """解锁 -> 10 01 -> security level 必须回到 LOCKED。"""
        import ecu

        self.unlock()
        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LEVEL1)

        handle_uds_request(bytes([0x10, 0x01]))

        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LOCKED)

    def test_session_switch_to_extended_locks(self) -> None:
        """解锁 -> 10 03 -> security level 必须回到 LOCKED。"""
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()
        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LEVEL1)

        handle_uds_request(bytes([0x10, 0x03]))

        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LOCKED)

    def test_session_switch_to_programming_locks(self) -> None:
        """
        解锁 -> 10 02 -> security level 必须回到 LOCKED。

        这是真实漏洞高发点：若不清除，攻击者可切回 default session
        而保持解锁状态。
        """
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()

        handle_uds_request(bytes([0x10, 0x02]))

        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LOCKED)

    def test_session_switch_clears_pending_seed(self) -> None:
        import ecu

        self.get_seed()
        self.assertIsNotNone(ecu.STATE.pending_seed)

        handle_uds_request(bytes([0x10, 0x03]))

        self.assertIsNone(ecu.STATE.pending_seed)
        self.assertFalse(ecu.STATE.seed_consumed)

    def test_replay_after_session_switch_is_rejected(self) -> None:
        """解锁后切会话，再重放原 key -> 7F 27 24。"""
        seed = self.unlock()
        key = keygen.derive_key(seed)

        handle_uds_request(bytes([0x10, 0x01]))

        response = self.send_key(key)

        self.assertNegativeResponse(
            response, SID_SECURITY_ACCESS, NRC_REQUEST_SEQUENCE_ERROR
        )

    def test_ecu_reset_locks(self) -> None:
        """解锁 -> 11 01 -> security level 必须回到 LOCKED。"""
        import ecu

        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()

        handle_uds_request(bytes([0x11, 0x01]))

        self.assertEqual(ecu.STATE.security_level, ecu.SECURITY_LOCKED)
        self.assertEqual(ecu.STATE.session, ecu.DEFAULT_SESSION)

    def test_ecu_reset_clears_pending_seed(self) -> None:
        import ecu

        self.get_seed()

        handle_uds_request(bytes([0x11, 0x01]))

        self.assertIsNone(ecu.STATE.pending_seed)
        self.assertFalse(ecu.STATE.seed_consumed)

    def test_lockout_survives_ecu_reset(self) -> None:
        """
        锁定状态在 ECUReset 后仍然存在。

        这是本教学 ECU 的明确设计选择：防止 Tester 通过复位 ECU
        刷掉暴力破解的惩罚状态。

        注意：**不是所有真实 ECU 都必然如此**，
        部分真实 ECU 会在复位时清空失败计数。
        """
        import ecu

        for _ in range(ecu.SECURITY_CONFIG["max_attempts"]):
            seed = self.get_seed()
            self.send_key(self.wrong_key_for(seed))

        attempts_before = ecu.STATE.failed_attempts

        handle_uds_request(bytes([0x11, 0x01]))

        self.assertEqual(ecu.STATE.failed_attempts, attempts_before)
        self.assertIsNotNone(ecu.STATE.lockout_until)
        self.assertNegativeResponse(
            self.request_seed(),
            SID_SECURITY_ACCESS,
            NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
        )

    def test_failed_attempts_survive_session_switch(self) -> None:
        """失败计数在会话切换后保留。"""
        import ecu

        seed = self.get_seed()
        self.send_key(self.wrong_key_for(seed))
        attempts_before = ecu.STATE.failed_attempts

        handle_uds_request(bytes([0x10, 0x03]))

        self.assertEqual(ecu.STATE.failed_attempts, attempts_before)


# ======================================================================
# 受保护 DID 的权限模型
# ======================================================================


class TestProtectedDidPermission(SecurityAccessTestCase):
    """Session → Security → Permission 三层权限。"""

    def read(self, did: int) -> bytes:
        return handle_uds_request(
            bytes([SID_READ_DATA_BY_IDENTIFIER, (did >> 8) & 0xFF, did & 0xFF])
        )

    def test_public_did_readable_in_default_locked(self) -> None:
        """公开 DID 在 default + locked 下可读。"""
        response = self.read(DID_F190)

        self.assertPositiveResponse(response, POS_READ_DATA_BY_IDENTIFIER)

    def test_protected_did_denied_in_default_locked(self) -> None:
        """Default + Locked -> 7F 22 33"""
        response = self.read(DID_F187_PROTECTED)

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_SECURITY_ACCESS_DENIED
        )

    def test_protected_did_denied_in_extended_locked(self) -> None:
        """Extended + Locked -> 7F 22 33"""
        handle_uds_request(bytes([0x10, 0x03]))

        response = self.read(DID_F187_PROTECTED)

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_SECURITY_ACCESS_DENIED
        )

    def test_protected_did_denied_in_programming_locked(self) -> None:
        handle_uds_request(bytes([0x10, 0x02]))

        response = self.read(DID_F187_PROTECTED)

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_SECURITY_ACCESS_DENIED
        )

    def test_protected_did_readable_in_extended_unlocked(self) -> None:
        """Extended + Level 1 -> 正常读取。"""
        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()

        response = self.read(DID_F187_PROTECTED)

        self.assertPositiveResponse(response, POS_READ_DATA_BY_IDENTIFIER)
        self.assertEqual(response[3:], DID_DATABASE[DID_F187_PROTECTED])

    def test_protected_did_denied_after_session_downgrade(self) -> None:
        """
        在 extended 解锁后切回 default，受保护 DID 必须重新被拒绝。

        这验证"会话切换清除安全等级"这一安全语义确实生效。
        """
        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()
        self.assertPositiveResponse(
            self.read(DID_F187_PROTECTED), POS_READ_DATA_BY_IDENTIFIER
        )

        handle_uds_request(bytes([0x10, 0x01]))

        self.assertNegativeResponse(
            self.read(DID_F187_PROTECTED),
            SID_READ_DATA_BY_IDENTIFIER,
            NRC_SECURITY_ACCESS_DENIED,
        )

    def test_protected_did_denied_after_ecu_reset(self) -> None:
        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()

        handle_uds_request(bytes([0x11, 0x01]))

        self.assertNegativeResponse(
            self.read(DID_F187_PROTECTED),
            SID_READ_DATA_BY_IDENTIFIER,
            NRC_SECURITY_ACCESS_DENIED,
        )

    def test_mixed_request_denied_as_a_whole(self) -> None:
        """
        多 DID 请求混合公开与受保护 DID 时整体拒绝。

        不返回部分数据，避免 Tester 误解析错位数据。
        """
        response = handle_uds_request(
            bytes([
                SID_READ_DATA_BY_IDENTIFIER,
                0xF1, 0x90,      # 公开 DID（可读）
                0xF1, 0x87,      # 受保护 DID（权限不足）
            ])
        )

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_SECURITY_ACCESS_DENIED
        )

    def test_mixed_request_denied_does_not_leak_public_data(self) -> None:
        """被拒绝的混合请求中不得包含任何公开 DID 的数据。"""
        response = handle_uds_request(
            bytes([SID_READ_DATA_BY_IDENTIFIER, 0xF1, 0x90, 0xF1, 0x87])
        )

        self.assertNotIn(
            DID_DATABASE[DID_F190],
            response,
            "被拒绝的响应中泄露了公开 DID 数据",
        )

    def test_mixed_request_succeeds_when_unlocked(self) -> None:
        """解锁后混合请求整体成功。"""
        handle_uds_request(bytes([0x10, 0x03]))
        self.unlock()

        response = handle_uds_request(
            bytes([SID_READ_DATA_BY_IDENTIFIER, 0xF1, 0x90, 0xF1, 0x87])
        )

        self.assertPositiveResponse(response, POS_READ_DATA_BY_IDENTIFIER)
        self.assertIn(DID_DATABASE[DID_F190], response)
        self.assertIn(DID_DATABASE[DID_F187_PROTECTED], response)

    def test_unknown_did_still_returns_out_of_range(self) -> None:
        """未知 DID 仍返回 0x31（权限检查不掩盖未知 DID）。"""
        response = self.read(DID_UNKNOWN)

        self.assertNegativeResponse(
            response, SID_READ_DATA_BY_IDENTIFIER, NRC_REQUEST_OUT_OF_RANGE
        )


class TestSessionTiming(SecurityAccessTestCase):
    """各会话返回不同的 P2 / P2* 时间参数。"""

    def test_default_session_timing_unchanged(self) -> None:
        """defaultSession 保持 00 32 / 00 64（兼容既有行为）。"""
        response = handle_uds_request(bytes([0x10, 0x01]))

        self.assertEqual(response, bytes([0x50, 0x01, 0x00, 0x32, 0x00, 0x64]))

    def test_extended_session_timing(self) -> None:
        response = handle_uds_request(bytes([0x10, 0x03]))

        self.assertEqual(response, bytes([0x50, 0x03, 0x00, 0x64, 0x03, 0xE8]))

    def test_programming_session_timing(self) -> None:
        response = handle_uds_request(bytes([0x10, 0x02]))

        self.assertEqual(response, bytes([0x50, 0x02, 0x00, 0xC8, 0x13, 0x88]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
