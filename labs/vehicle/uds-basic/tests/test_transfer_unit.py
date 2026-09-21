#!/usr/bin/env python3
"""
UDS Basic —— 0x34/0x36/0x37 download transfer 离线单元测试

直接调用协议层 ``uds_server``，不依赖 vcan0 / SocketCAN / ISO-TP。

覆盖（atomic transfer feature scope）:

1. 静态配置约束（MAX_DOWNLOAD_SIZE=65535、请求/数据长度 128/126、DFI/ALFID）
2. 0x34 RequestDownload：valid / 长度错 / DFI/ALFID 不支持 / size=0 /
   size=65535 / 内部超限护栏 / 已有 active → 0x22；错误路径状态不变
3. 0x36 TransferData：before 0x34 / 单块 / 多块 / 126 边界 / 127 拒绝 /
   请求 129 拒绝 / 错序与跳号 → 0x73 / 重复块幂等（含连续重复）/
   FF→00 回绕 / 越界块 → 0x13
4. 0x37 RequestTransferExit：idle → 0x24 / incomplete → 0x22（保持 active）/
   complete → 77 + 全清 / 多余字节 → 0x13
5. 生命周期：hardReset 四路语义（transfer 清 / routine 清 / DID 清 /
   DTC 保留）；session 切换保持 active；0x37 成功后回 IDLE
6. 实例隔离与 bytearray 默认值不共享

明确**不测**：0x35 / 0x38 / 0x39、真实 Flash、签名校验、
Permission/session framework（0x33/0x7E/0x7F）。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_transfer_unit.py

或：

    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

from transfer_database import (  # noqa: E402
    MAX_DOWNLOAD_SIZE,
    MAX_TRANSFER_DATA_LENGTH,
    MAX_TRANSFER_REQUEST_LENGTH,
    REQUEST_DOWNLOAD_ALFID,
    REQUEST_DOWNLOAD_DFI,
)
from uds_server import (  # noqa: E402
    EcuState,
    _validate_download_size,
    handle_uds_request,
)

SID_REQUEST_DOWNLOAD = 0x34
SID_TRANSFER_DATA = 0x36
SID_REQUEST_TRANSFER_EXIT = 0x37

NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_WRONG_BLOCK_SEQUENCE_COUNTER = 0x73

POS_REQUEST_DOWNLOAD = 0x74
POS_TRANSFER_DATA = 0x76
POS_REQUEST_TRANSFER_EXIT = 0x77


def _download(dfi=REQUEST_DOWNLOAD_DFI, alfid=REQUEST_DOWNLOAD_ALFID,
              address=0x112233, size=8) -> bytes:
    return bytes([
        SID_REQUEST_DOWNLOAD, dfi, alfid,
        (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF,
        (size >> 8) & 0xFF, size & 0xFF,
    ])


def _block(counter: int, data: bytes) -> bytes:
    return bytes([SID_TRANSFER_DATA, counter]) + data


class TransferTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    def setUp(self) -> None:
        self.state = EcuState()

    def snapshot(self) -> tuple:
        return (
            self.state.download_active,
            self.state.download_address,
            self.state.download_size,
            self.state.download_expected_block,
            bytes(self.state.download_buffer),
            self.state.download_last_block_request,
        )

    def start_download(self, size: int = 8, address: int = 0x112233) -> bytes:
        return handle_uds_request(_download(address=address, size=size), self.state)


# ----------------------------------------------------------------------
# 1. 静态配置约束
# ----------------------------------------------------------------------


class TestTransferDatabase(TransferTestCase):

    def test_max_download_size_is_wire_max(self) -> None:
        """MAX_DOWNLOAD_SIZE 必须等于 2 字节字段最大可编码值。"""
        self.assertEqual(MAX_DOWNLOAD_SIZE, 0xFFFF)

    def test_lengths_relation(self) -> None:
        """128 = 完整请求长度；126 = 纯数据长度（128 − SID − BSC）。"""
        self.assertEqual(
            MAX_TRANSFER_REQUEST_LENGTH - 2, MAX_TRANSFER_DATA_LENGTH)

    def test_alfid_encoding(self) -> None:
        """ALFID 高半字节=sizeLength(2)、低半字节=addressLength(3)。"""
        self.assertEqual(REQUEST_DOWNLOAD_ALFID, 0x23)

    def test_dfi_is_zero(self) -> None:
        self.assertEqual(REQUEST_DOWNLOAD_DFI, 0x00)

    def test_size_validator_boundaries(self) -> None:
        self.assertTrue(_validate_download_size(1))
        self.assertTrue(_validate_download_size(65535))
        self.assertFalse(_validate_download_size(0))
        # 内部护栏：wire 2 字节字段无法编码 65536，但 helper 必须拒绝
        self.assertFalse(_validate_download_size(65536))

    def test_new_state_transfer_fields_defaults(self) -> None:
        self.assertFalse(self.state.download_active)
        self.assertEqual(self.state.download_address, 0)
        self.assertEqual(self.state.download_size, 0)
        self.assertEqual(self.state.download_expected_block, 0)
        self.assertEqual(self.state.download_buffer, bytearray())
        self.assertIsNone(self.state.download_last_block_request)


# ----------------------------------------------------------------------
# 2. 0x34 RequestDownload
# ----------------------------------------------------------------------


class TestRequestDownload(TransferTestCase):

    def test_valid_exact_response(self) -> None:
        response = self.start_download(size=8, address=0x112233)

        self.assertEqual(response, bytes([0x74, 0x20, 0x00, 0x80]))

    def test_valid_establishes_state(self) -> None:
        self.start_download(size=8, address=0x112233)

        self.assertTrue(self.state.download_active)
        self.assertEqual(self.state.download_address, 0x112233)
        self.assertEqual(self.state.download_size, 8)
        self.assertEqual(self.state.download_expected_block, 1)
        self.assertEqual(self.state.download_buffer, bytearray())
        self.assertIsNone(self.state.download_last_block_request)

    def test_max_size_65535_accepted(self) -> None:
        response = self.start_download(size=65535)

        self.assertEqual(response, bytes([0x74, 0x20, 0x00, 0x80]))
        self.assertEqual(self.state.download_size, 65535)

    def test_wrong_length(self) -> None:
        cases = (
            (6, _download()[:6]),
            (7, _download()[:7]),
            (9, _download() + b"\x00"),
        )

        for length, request in cases:
            with self.subTest(length=length):
                response = handle_uds_request(request, self.state)
                self.assertEqual(
                    response,
                    bytes([0x7F, SID_REQUEST_DOWNLOAD,
                           NRC_INCORRECT_MESSAGE_LENGTH]),
                )

    def test_unsupported_dfi(self) -> None:
        response = handle_uds_request(_download(dfi=0x01), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_DOWNLOAD, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_unsupported_alfid(self) -> None:
        for alfid in (0x32, 0x33, 0x22, 0xFF):
            with self.subTest(alfid=alfid):
                response = handle_uds_request(_download(alfid=alfid), self.state)
                self.assertEqual(
                    response,
                    bytes([0x7F, SID_REQUEST_DOWNLOAD,
                           NRC_REQUEST_OUT_OF_RANGE]),
                )

    def test_zero_size(self) -> None:
        response = handle_uds_request(_download(size=0), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_DOWNLOAD, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_download_while_active(self) -> None:
        self.start_download()
        before = self.snapshot()

        response = self.start_download()

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_DOWNLOAD, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.snapshot(), before, "0x22 不得改变状态")

    def test_new_download_resets_previous_session(self) -> None:
        """0x37 成功后再次 0x34：必须建立全新 session，无残留。"""
        self.start_download(size=4)
        handle_uds_request(_block(1, b"\x01\x02\x03\x04"), self.state)
        handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state)

        response = self.start_download(size=4)
        self.assertEqual(response, bytes([0x74, 0x20, 0x00, 0x80]))
        self.assertEqual(self.state.download_expected_block, 1)
        self.assertEqual(self.state.download_buffer, bytearray())
        self.assertIsNone(self.state.download_last_block_request)

    def test_all_nrc_paths_do_not_mutate_state(self) -> None:
        before = self.snapshot()

        handle_uds_request(_download(dfi=0x01), self.state)
        handle_uds_request(_download(alfid=0x32), self.state)
        handle_uds_request(_download(size=0), self.state)
        handle_uds_request(_download()[:7], self.state)

        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 3. 0x36 TransferData
# ----------------------------------------------------------------------


class TestTransferData(TransferTestCase):

    def test_before_download(self) -> None:
        before = self.snapshot()

        response = handle_uds_request(_block(1, b"\x01"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_single_block(self) -> None:
        self.start_download(size=4)

        response = handle_uds_request(_block(1, b"\xAA\xBB\xCC\xDD"), self.state)

        self.assertEqual(response, bytes([0x76, 0x01]))
        self.assertEqual(self.state.download_buffer, bytearray(b"\xAA\xBB\xCC\xDD"))
        self.assertEqual(self.state.download_expected_block, 2)
        self.assertEqual(self.state.download_last_block_request, _block(1, b"\xAA\xBB\xCC\xDD"))

    def test_multiple_blocks(self) -> None:
        self.start_download(size=6)

        self.assertEqual(
            handle_uds_request(_block(1, b"\x01\x02"), self.state),
            bytes([0x76, 0x01]))
        self.assertEqual(
            handle_uds_request(_block(2, b"\x03\x04"), self.state),
            bytes([0x76, 0x02]))
        self.assertEqual(
            handle_uds_request(_block(3, b"\x05\x06"), self.state),
            bytes([0x76, 0x03]))

        self.assertEqual(
            self.state.download_buffer, bytearray(b"\x01\x02\x03\x04\x05\x06"))
        self.assertEqual(self.state.download_expected_block, 4)

    def test_data_length_126_boundary(self) -> None:
        self.start_download(size=126)

        response = handle_uds_request(_block(1, bytes(126)), self.state)

        self.assertEqual(response, bytes([0x76, 0x01]))
        self.assertEqual(len(self.state.download_buffer), 126)

    def test_data_length_127_rejected(self) -> None:
        self.start_download(size=127)
        before = self.snapshot()

        response = handle_uds_request(_block(1, bytes(127)), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_request_length_129_rejected(self) -> None:
        self.start_download(size=200)
        before = self.snapshot()

        response = handle_uds_request(_block(1, bytes(127 + 1)), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_request_too_short(self) -> None:
        self.start_download(size=4)

        self.assertEqual(
            handle_uds_request(bytes([0x36, 0x01]), self.state),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_wrong_counter(self) -> None:
        self.start_download(size=4)
        before = self.snapshot()

        response = handle_uds_request(_block(2, b"\x01\x02"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA,
                   NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_jump_counter(self) -> None:
        self.start_download(size=4)
        before = self.snapshot()

        response = handle_uds_request(_block(0x80, b"\x01"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA,
                   NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_previous_block_idempotent(self) -> None:
        self.start_download(size=4)

        handle_uds_request(_block(1, b"\xAA\xBB"), self.state)
        after_first = self.snapshot()

        response = handle_uds_request(_block(1, b"\xAA\xBB"), self.state)

        self.assertEqual(response, bytes([0x76, 0x01]))
        self.assertEqual(self.snapshot(), after_first,
                         "重复块不得追加 buffer、不得推进 counter")

    def test_repeated_duplicates_stay_idempotent(self) -> None:
        self.start_download(size=4)

        handle_uds_request(_block(1, b"\xAA\xBB"), self.state)
        expected = self.snapshot()

        for _ in range(3):
            response = handle_uds_request(_block(1, b"\xAA\xBB"), self.state)
            self.assertEqual(response, bytes([0x76, 0x01]))
            self.assertEqual(self.snapshot(), expected)

        # 幂等重传后，正确的新块仍然可用（expected 未被推进）
        self.assertEqual(
            handle_uds_request(_block(2, b"\xCC\xDD"), self.state),
            bytes([0x76, 0x02]))
        self.assertEqual(
            self.state.download_buffer, bytearray(b"\xAA\xBB\xCC\xDD"))

    def test_same_counter_different_data_is_sequence_error(self) -> None:
        self.start_download(size=4)

        handle_uds_request(_block(1, b"\xAA\xBB"), self.state)
        before = self.snapshot()

        # 相同 BSC 但数据不同：不是完全重复 → 真正的 sequence error
        response = handle_uds_request(_block(1, b"\xCC\xDD"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA,
                   NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_counter_wrap_ff_to_00(self) -> None:
        self.start_download(size=256 + 4)

        counter = 1
        for value in range(1, 256):
            response = handle_uds_request(_block(counter, bytes([value])), self.state)
            self.assertEqual(response, bytes([0x76, counter & 0xFF]))
            counter = (counter + 1) & 0xFF
            self.assertEqual(self.state.download_expected_block, counter)

        # 第 255 块（BSC=0xFF）成功后 expected 回绕到 0x00
        response = handle_uds_request(_block(0xFF, bytes([0xFF])), self.state)
        self.assertEqual(response, bytes([0x76, 0xFF]))
        self.assertEqual(self.state.download_expected_block, 0x00)

        response = handle_uds_request(_block(0x00, bytes([0x01])), self.state)
        self.assertEqual(response, bytes([0x76, 0x00]))
        self.assertEqual(self.state.download_expected_block, 0x01)

    def test_overflow_against_requested_size(self) -> None:
        self.start_download(size=4)

        handle_uds_request(_block(1, b"\x01\x02"), self.state)
        before = self.snapshot()

        # 剩余容量 2，发 3 字节 → 越界
        response = handle_uds_request(_block(2, b"\x03\x04\x05"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )
        self.assertEqual(self.snapshot(), before,
                         "越界块不得改变 buffer/counter/last_block")

    def test_after_transfer_exit(self) -> None:
        self.start_download(size=2)
        handle_uds_request(_block(1, b"\x01\x02"), self.state)
        handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state)

        response = handle_uds_request(_block(1, b"\x03"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )


# ----------------------------------------------------------------------
# 4. 0x37 RequestTransferExit
# ----------------------------------------------------------------------


class TestRequestTransferExit(TransferTestCase):

    def test_idle(self) -> None:
        before = self.snapshot()

        response = handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_keeps_active(self) -> None:
        self.start_download(size=8)
        handle_uds_request(_block(1, b"\x01\x02"), self.state)

        response = handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertTrue(self.state.download_active, "未完成时必须保持 active")

        # 可继续传完再成功退出
        handle_uds_request(_block(2, b"\x03\x04\x05\x06\x07\x08"), self.state)
        self.assertEqual(
            handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state),
            bytes([POS_REQUEST_TRANSFER_EXIT]),
        )

    def test_complete_clears_all_transfer_state(self) -> None:
        self.start_download(size=4)
        handle_uds_request(_block(1, b"\xAA\xBB"), self.state)
        handle_uds_request(_block(2, b"\xCC\xDD"), self.state)

        response = handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state)

        self.assertEqual(response, bytes([POS_REQUEST_TRANSFER_EXIT]))
        self.assertFalse(self.state.download_active)
        self.assertEqual(self.state.download_address, 0)
        self.assertEqual(self.state.download_size, 0)
        self.assertEqual(self.state.download_expected_block, 0)
        self.assertEqual(self.state.download_buffer, bytearray())
        self.assertIsNone(self.state.download_last_block_request)

    def test_extra_bytes(self) -> None:
        self.start_download(size=4)
        handle_uds_request(_block(1, b"\xAA\xBB\xCC\xDD"), self.state)

        response = handle_uds_request(
            bytes([SID_REQUEST_TRANSFER_EXIT, 0x00]), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT,
                   NRC_INCORRECT_MESSAGE_LENGTH]),
        )


# ----------------------------------------------------------------------
# 5. 生命周期
# ----------------------------------------------------------------------


class TestTransferLifecycle(TransferTestCase):

    def test_hard_reset_clears_transfer_preserves_dtc(self) -> None:
        self.start_download(size=4)
        handle_uds_request(_block(1, b"\xAA\xBB"), self.state)
        # 同时制造 routine 与 DTC 状态，做四路成对断言
        handle_uds_request(bytes([0x31, 0x01, 0x20, 0x01]), self.state)
        self.state.dtc_records[0x100009] = 0x09
        dtc_before = dict(self.state.dtc_records)

        handle_uds_request(bytes([0x11, 0x01]), self.state)

        # transfer：清
        self.assertFalse(self.state.download_active)
        self.assertEqual(self.state.download_buffer, bytearray())
        self.assertEqual(self.state.download_expected_block, 0)
        self.assertIsNone(self.state.download_last_block_request)
        # routine：清
        self.assertEqual(self.state.active_routines, set())
        self.assertEqual(self.state.routine_start_counts, {})
        # DTC：保留
        self.assertEqual(self.state.dtc_records, dtc_before)
        # 复位后 transfer 回 IDLE
        self.assertEqual(
            handle_uds_request(_block(1, b"\x01"), self.state),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_session_switch_preserves_download(self) -> None:
        self.start_download(size=4)

        handle_uds_request(bytes([0x10, 0x03]), self.state)
        self.assertEqual(self.state.session, 0x03)

        # download 保持 active，0x36 继续成功
        self.assertTrue(self.state.download_active)
        self.assertEqual(
            handle_uds_request(_block(1, b"\xAA\xBB"), self.state),
            bytes([0x76, 0x01]))
        self.assertEqual(
            handle_uds_request(_block(2, b"\xCC\xDD"), self.state),
            bytes([0x76, 0x02]))
        self.assertEqual(
            handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state),
            bytes([POS_REQUEST_TRANSFER_EXIT]),
        )

    def test_instance_isolation(self) -> None:
        other = EcuState()

        self.start_download(size=4)
        handle_uds_request(_block(1, b"\xAA\xBB"), self.state)

        self.assertFalse(other.download_active)
        self.assertEqual(other.download_buffer, bytearray())
        self.assertIsNone(other.download_last_block_request)

    def test_bytearray_default_not_shared(self) -> None:
        first = EcuState()
        second = EcuState()

        first.download_buffer.extend(b"\x01")

        self.assertEqual(second.download_buffer, bytearray())


if __name__ == "__main__":
    unittest.main(verbosity=2)
