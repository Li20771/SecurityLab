#!/usr/bin/env python3
"""
UDS Basic —— 0x2E WriteDataByIdentifier / 0x3E TesterPresent 离线单元测试

直接调用协议层 ``uds_server.handle_uds_request(request, state)``，
每个用例使用独立的 ``EcuState``，不依赖 vcan0 / SocketCAN / ISO-TP / 0x27
注入流程（解锁状态在用例内直接设置，以聚焦服务自身的门控与状态语义）。

运行：

    python3 tests/test_services_extra_unit.py
或：
    bash tests/run_tests.sh
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import uds_types  # noqa: E402
from did_database import DID_DATABASE, WRITABLE_DID_LENGTHS  # noqa: E402
from uds_server import EcuState, handle_uds_request  # noqa: E402

# SID / 正响应 SID
SID_WRITE = 0x2E
POS_WRITE = 0x6E
SID_TESTER_PRESENT = 0x3E
POS_TESTER_PRESENT = 0x7E
SID_READ = 0x22
POS_READ = 0x62
SID_RESET = 0x11

DID_WRITABLE = 0xF102
DID_READONLY = 0xF190      # VIN，已知但只读
DID_UNKNOWN = 0xFFFF

WRITABLE_MIN, WRITABLE_MAX = WRITABLE_DID_LENGTHS[DID_WRITABLE]


def neg(sid: int, nrc: int) -> bytes:
    return bytes([0x7F, sid, nrc])


def unlock_extended(state: EcuState) -> None:
    """把状态置为 extendedDiagnosticSession + SecurityAccess LEVEL1。"""
    state.session = uds_types.SESSION_EXTENDED
    state.security_level = uds_types.SECURITY_LEVEL1


class TesterPresentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = EcuState()

    def test_zero_subfunction_positive(self):
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x00]), self.state),
            bytes([POS_TESTER_PRESENT, 0x00]),
        )

    def test_suppress_bit_returns_empty(self):
        # 3E 80：抑制正响应，协议层回传 b""，入口层不得发送
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x80]), self.state),
            b"",
        )

    def test_unsupported_subfunction(self):
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x01]), self.state),
            neg(SID_TESTER_PRESENT, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED),
        )

    def test_suppress_bit_with_nonzero_subfunction_still_rejected(self):
        # 0x81 = suppress(bit7) + subfunction 1：剥离 bit7 后非 0，仍 0x12
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x81]), self.state),
            neg(SID_TESTER_PRESENT, uds_types.NRC_SUBFUNCTION_NOT_SUPPORTED),
        )

    def test_bad_lengths(self):
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT]), self.state),
            neg(SID_TESTER_PRESENT, uds_types.NRC_INCORRECT_MESSAGE_LENGTH),
        )
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x00, 0x01]), self.state),
            neg(SID_TESTER_PRESENT, uds_types.NRC_INCORRECT_MESSAGE_LENGTH),
        )

    def test_available_in_default_session(self):
        # TesterPresent 在默认会话即可用
        self.assertEqual(self.state.session, uds_types.SESSION_DEFAULT)
        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x00]), self.state),
            bytes([POS_TESTER_PRESENT, 0x00]),
        )

    def test_does_not_mutate_state(self):
        unlock_extended(self.state)
        before = (self.state.session, self.state.security_level)
        handle_uds_request(bytes([SID_TESTER_PRESENT, 0x00]), self.state)
        after = (self.state.session, self.state.security_level)
        self.assertEqual(before, after)


class WriteDataByIdentifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = EcuState()

    def _write(self, did: int, data: bytes) -> bytes:
        return handle_uds_request(
            bytes([SID_WRITE, (did >> 8) & 0xFF, did & 0xFF]) + data, self.state
        )

    def _read(self, did: int) -> bytes:
        return handle_uds_request(
            bytes([SID_READ, (did >> 8) & 0xFF, did & 0xFF]), self.state
        )

    def test_default_session_rejected(self):
        resp = self._write(DID_WRITABLE, b"NEW")
        self.assertEqual(
            resp, neg(SID_WRITE, uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION)
        )

    def test_extended_locked_rejected(self):
        self.state.session = uds_types.SESSION_EXTENDED
        # 仍处于 LOCKED
        resp = self._write(DID_WRITABLE, b"NEW")
        self.assertEqual(resp, neg(SID_WRITE, uds_types.NRC_SECURITY_ACCESS_DENIED))

    def test_unlocked_write_then_readback(self):
        unlock_extended(self.state)
        self.assertEqual(
            self._write(DID_WRITABLE, b"NEW_CONF"),
            bytes([POS_WRITE, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF]),
        )
        # 0x22 立即可读到新值（默认会话也能读该 DID，此处仍处于 extended）
        self.assertEqual(
            self._read(DID_WRITABLE),
            bytes([POS_READ, (DID_WRITABLE >> 8) & 0xFF, DID_WRITABLE & 0xFF])
            + b"NEW_CONF",
        )

    def test_unknown_did_out_of_range(self):
        unlock_extended(self.state)
        self.assertEqual(
            self._write(DID_UNKNOWN, b"X"),
            neg(SID_WRITE, uds_types.NRC_REQUEST_OUT_OF_RANGE),
        )

    def test_readonly_did_conditions_not_correct(self):
        unlock_extended(self.state)
        self.assertEqual(
            self._write(DID_READONLY, b"X"),
            neg(SID_WRITE, uds_types.NRC_CONDITIONS_NOT_CORRECT),
        )

    def test_length_validation(self):
        unlock_extended(self.state)
        # 完全没有数据字节
        self.assertEqual(
            handle_uds_request(bytes([SID_WRITE, 0xF1, 0x02]), self.state),
            neg(SID_WRITE, uds_types.NRC_INCORRECT_MESSAGE_LENGTH),
        )
        # 超过最大长度
        too_long = b"A" * (WRITABLE_MAX + 1)
        self.assertEqual(
            self._write(DID_WRITABLE, too_long),
            neg(SID_WRITE, uds_types.NRC_INCORRECT_MESSAGE_LENGTH),
        )

    def test_length_boundaries_ok(self):
        unlock_extended(self.state)
        want_pos = bytes([POS_WRITE, 0xF1, 0x02])
        self.assertEqual(self._write(DID_WRITABLE, b"A" * WRITABLE_MIN), want_pos)
        self.assertEqual(self._write(DID_WRITABLE, b"B" * WRITABLE_MAX), want_pos)

    def test_programming_session_unlocked_allowed(self):
        # 门控只拒绝 defaultSession；programming + 解锁同样允许
        self.state.session = uds_types.SESSION_PROGRAMMING
        self.state.security_level = uds_types.SECURITY_LEVEL1
        self.assertEqual(
            self._write(DID_WRITABLE, b"PRG"), bytes([POS_WRITE, 0xF1, 0x02])
        )

    def test_ecu_reset_restores_factory_default(self):
        unlock_extended(self.state)
        self._write(DID_WRITABLE, b"CHANGED")
        self.assertEqual(
            self._read(DID_WRITABLE), bytes([POS_READ, 0xF1, 0x02]) + b"CHANGED"
        )
        # hardReset：会话回 default、清 did_overrides
        self.assertEqual(
            handle_uds_request(bytes([SID_RESET, 0x01]), self.state),
            bytes([0x51, 0x01]),
        )
        self.assertEqual(
            self._read(DID_WRITABLE),
            bytes([POS_READ, 0xF1, 0x02]) + DID_DATABASE[DID_WRITABLE],
        )

    def test_write_does_not_mutate_factory_database(self):
        original = DID_DATABASE[DID_WRITABLE]
        unlock_extended(self.state)
        self._write(DID_WRITABLE, b"RUNTIME")
        # 出厂表保持不变，运行时值只进 state.did_overrides
        self.assertEqual(DID_DATABASE[DID_WRITABLE], original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
