#!/usr/bin/env python3
"""
UDS Basic —— 0x35 RequestUpload 离线单元测试

直接调用协议层 ``uds_server``，不依赖 vcan0 / SocketCAN / ISO-TP。

覆盖（按已冻结的 Design Review v3）：

1. 0x35：valid byte-exact / 长度错 / DFI / ALFID / size=0 /
   size=32130 / size=32131 / 双向互斥（状态不变）
2. upload initial：35 后立即 36 00 → 73（sent==0 不判 duplicate）；
   36 01 → 第一块
3. duplicate：重复重发同一块（sent/expected 不变）、连续重复幂等
4. multi-block（size=130 → 126+4）：sent 0→126→130、数据逐字节
5. final-block duplicate：尾块重发字节一致
6. 32130 边界：255 块（BSC 01…FF）→ sent=32130、expected=0x00；
   36 00 → 22（remaining==0 护栏）；37 → 77
7. wrong BSC：跳号/回退 → 73 状态不变
8. 0x37：incomplete → 22 / complete → 77 + 5 字段全清 / 之后 36/37 → 24
9. 34↔35 互斥四向 → 22 状态不变
10. precedence：无 active 任意形态 36 → 24；upload active 3 字节 → 13；
    download active 2 字节 → 13；无 active 37 1 字节 → 24；
    无 active 37 2 字节 → 13；upload active 37 2 字节 → 13
11. lifecycle：hardReset 清 5 字段（五路语义）、session 保持、
    实例隔离、默认值隔离

明确**不测**：0x38 / 0x39、真实 Flash / firmware 文件、签名校验、
Permission / session framework。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_upload_unit.py

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
    MAX_TRANSFER_DATA_LENGTH,
    MAX_UPLOAD_SIZE,
    UPLOAD_SOURCE,
    _upload_chunk,
)
from uds_server import (  # noqa: E402
    EcuState,
    _validate_upload_size,
    handle_uds_request,
)

SID_REQUEST_DOWNLOAD = 0x34
SID_REQUEST_UPLOAD = 0x35
SID_TRANSFER_DATA = 0x36
SID_REQUEST_TRANSFER_EXIT = 0x37

NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_SEQUENCE_ERROR = 0x24
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_WRONG_BLOCK_SEQUENCE_COUNTER = 0x73

POS_REQUEST_UPLOAD = 0x75
POS_TRANSFER_DATA = 0x76
POS_REQUEST_TRANSFER_EXIT = 0x77


def _upload(dfi=0x00, alfid=0x23, address=0x112233, size=130) -> bytes:
    return bytes([
        SID_REQUEST_UPLOAD, dfi, alfid,
        (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF,
        (size >> 8) & 0xFF, size & 0xFF,
    ])


def _download(dfi=0x00, alfid=0x23, address=0x112233, size=8) -> bytes:
    return bytes([
        SID_REQUEST_DOWNLOAD, dfi, alfid,
        (address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF,
        (size >> 8) & 0xFF, size & 0xFF,
    ])


def _block_request(counter: int, data: bytes = b"") -> bytes:
    return bytes([SID_TRANSFER_DATA, counter]) + data


class UploadTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    def setUp(self) -> None:
        self.state = EcuState()

    def snapshot(self) -> tuple:
        return (
            self.state.upload_active,
            self.state.upload_address,
            self.state.upload_size,
            self.state.upload_expected_block,
            self.state.upload_sent,
        )

    def start_upload(self, size: int = 130, address: int = 0x112233) -> bytes:
        return handle_uds_request(
            _upload(address=address, size=size), self.state)


# ----------------------------------------------------------------------
# 1. 常量 / source helper
# ----------------------------------------------------------------------


class TestUploadSource(UploadTestCase):

    def test_upload_source_is_256_byte_pattern(self) -> None:
        self.assertEqual(len(UPLOAD_SOURCE), 256)
        self.assertEqual(UPLOAD_SOURCE[:4], bytes([0x00, 0x01, 0x02, 0x03]))

    def test_upload_chunk_basic(self) -> None:
        self.assertEqual(_upload_chunk(0, 4), bytes([0x00, 0x01, 0x02, 0x03]))

    def test_upload_chunk_wraps_at_256(self) -> None:
        self.assertEqual(_upload_chunk(254, 4), bytes([0xFE, 0xFF, 0x00, 0x01]))

    def test_upload_chunk_deterministic(self) -> None:
        self.assertEqual(_upload_chunk(126, 4), _upload_chunk(126, 4))

    def test_upload_chunk_does_not_mutate_source(self) -> None:
        before = bytes(UPLOAD_SOURCE)

        _upload_chunk(0, 300)

        self.assertEqual(UPLOAD_SOURCE, before)

    def test_validate_upload_size_boundaries(self) -> None:
        self.assertTrue(_validate_upload_size(1))
        self.assertTrue(_validate_upload_size(MAX_UPLOAD_SIZE))
        self.assertFalse(_validate_upload_size(0))
        self.assertFalse(_validate_upload_size(MAX_UPLOAD_SIZE + 1))

    def test_max_upload_size_is_255_blocks(self) -> None:
        self.assertEqual(MAX_UPLOAD_SIZE, 255 * MAX_TRANSFER_DATA_LENGTH)

    def test_new_state_upload_fields_defaults(self) -> None:
        self.assertFalse(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.upload_size, 0)
        self.assertEqual(self.state.upload_expected_block, 0)
        self.assertEqual(self.state.upload_sent, 0)


# ----------------------------------------------------------------------
# 2. 0x35 RequestUpload
# ----------------------------------------------------------------------


class TestRequestUpload(UploadTestCase):

    def test_valid_exact_response(self) -> None:
        response = self.start_upload(size=130)

        self.assertEqual(response, bytes([0x75, 0x20, 0x00, 0x80]))

    def test_valid_establishes_state(self) -> None:
        self.start_upload(size=130, address=0x112233)

        self.assertTrue(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0x112233)
        self.assertEqual(self.state.upload_size, 130)
        self.assertEqual(self.state.upload_expected_block, 1)
        self.assertEqual(self.state.upload_sent, 0)

    def test_wrong_length(self) -> None:
        cases = ((6, _upload()[:6]), (7, _upload()[:7]), (9, _upload() + b"\x00"))

        for length, request in cases:
            with self.subTest(length=length):
                response = handle_uds_request(request, self.state)
                self.assertEqual(
                    response,
                    bytes([0x7F, SID_REQUEST_UPLOAD,
                           NRC_INCORRECT_MESSAGE_LENGTH]),
                )

    def test_bad_dfi(self) -> None:
        response = handle_uds_request(_upload(dfi=0x01), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_UPLOAD, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_bad_alfid(self) -> None:
        for alfid in (0x32, 0x33, 0x22, 0xFF):
            with self.subTest(alfid=alfid):
                response = handle_uds_request(_upload(alfid=alfid), self.state)
                self.assertEqual(
                    response,
                    bytes([0x7F, SID_REQUEST_UPLOAD,
                           NRC_REQUEST_OUT_OF_RANGE]),
                )

    def test_zero_size(self) -> None:
        response = handle_uds_request(_upload(size=0), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_UPLOAD, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_size_32130_accepted(self) -> None:
        response = self.start_upload(size=MAX_UPLOAD_SIZE)

        self.assertEqual(response, bytes([0x75, 0x20, 0x00, 0x80]))
        self.assertEqual(self.state.upload_size, MAX_UPLOAD_SIZE)

    def test_size_32131_rejected(self) -> None:
        response = handle_uds_request(
            _upload(size=MAX_UPLOAD_SIZE + 1), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_UPLOAD, NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_upload_while_download_active(self) -> None:
        handle_uds_request(_download(), self.state)
        before = self.snapshot() + (self.state.download_active,
                                    bytes(self.state.download_buffer))

        response = self.start_upload()

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_UPLOAD, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.snapshot() + (self.state.download_active,
                                            bytes(self.state.download_buffer)),
                         before, "0x22 不得改变任一方向状态")

    def test_upload_while_upload_active(self) -> None:
        self.start_upload()
        before = self.snapshot()

        response = self.start_upload()

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_UPLOAD, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_download_while_upload_active(self) -> None:
        self.start_upload()
        before = self.snapshot() + (self.state.download_active,
                                    bytes(self.state.download_buffer))

        response = handle_uds_request(_download(), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_DOWNLOAD, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.snapshot() + (self.state.download_active,
                                            bytes(self.state.download_buffer)),
                         before)

    def test_all_35_nrc_paths_do_not_mutate_state(self) -> None:
        before = self.snapshot()

        handle_uds_request(_upload(dfi=0x01), self.state)
        handle_uds_request(_upload(alfid=0x32), self.state)
        handle_uds_request(_upload(size=0), self.state)
        handle_uds_request(_upload()[:7], self.state)
        handle_uds_request(_upload(size=MAX_UPLOAD_SIZE + 1), self.state)

        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 3. upload initial / 4. duplicate / 5. multi-block
# ----------------------------------------------------------------------


class TestUploadTransferData(UploadTestCase):

    def test_initial_bsc_00_is_sequence_error_not_duplicate(self) -> None:
        """Blocker 1 回归：35 后 sent==0，36 00 不能判 duplicate。"""
        self.start_upload(size=130)
        before = self.snapshot()

        response = handle_uds_request(_block_request(0x00), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA,
                   NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_first_block_36_01(self) -> None:
        self.start_upload(size=130)

        response = handle_uds_request(_block_request(0x01), self.state)

        self.assertEqual(response, bytes([0x76, 0x01]) + _upload_chunk(0, 126))
        self.assertEqual(self.state.upload_sent, 126)
        self.assertEqual(self.state.upload_expected_block, 2)

    def test_duplicate_first_block_identical_data(self) -> None:
        self.start_upload(size=130)

        first = handle_uds_request(_block_request(0x01), self.state)
        after_first = self.snapshot()

        duplicate = handle_uds_request(_block_request(0x01), self.state)

        self.assertEqual(duplicate, first)
        self.assertEqual(self.snapshot(), after_first,
                         "duplicate 不得推进 sent/expected")

    def test_repeated_duplicates_stay_idempotent(self) -> None:
        self.start_upload(size=130)

        first = handle_uds_request(_block_request(0x01), self.state)
        expected = self.snapshot()

        for _ in range(3):
            response = handle_uds_request(_block_request(0x01), self.state)
            self.assertEqual(response, first)
            self.assertEqual(self.snapshot(), expected)

    def test_multiblock_130_sent_accounting(self) -> None:
        """size=130 → 126 + 4；sent: 0 → 126 → 130；数据逐字节。"""
        self.start_upload(size=130)

        self.assertEqual(self.state.upload_sent, 0)

        block1 = handle_uds_request(_block_request(0x01), self.state)
        self.assertEqual(block1, bytes([0x76, 0x01]) + _upload_chunk(0, 126))
        self.assertEqual(self.state.upload_sent, 126)

        block2 = handle_uds_request(_block_request(0x02), self.state)
        self.assertEqual(block2, bytes([0x76, 0x02]) + _upload_chunk(126, 4))
        self.assertEqual(self.state.upload_sent, 130)
        self.assertEqual(self.state.upload_expected_block, 3)

    def test_final_block_duplicate_reconstructs_identical_data(self) -> None:
        """尾块（4 字节）duplicate：确定性重建，与首发送逐字节一致。"""
        self.start_upload(size=130)

        handle_uds_request(_block_request(0x01), self.state)
        tail = handle_uds_request(_block_request(0x02), self.state)
        after_tail = self.snapshot()

        duplicate = handle_uds_request(_block_request(0x02), self.state)

        self.assertEqual(duplicate, tail)
        self.assertEqual(duplicate, bytes([0x76, 0x02]) + _upload_chunk(126, 4))
        self.assertEqual(self.snapshot(), after_tail)

    def test_wrong_bsc_jump(self) -> None:
        self.start_upload(size=130)
        before = self.snapshot()

        response = handle_uds_request(_block_request(0x05), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA,
                   NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_wrong_bsc_backward(self) -> None:
        self.start_upload(size=130)

        handle_uds_request(_block_request(0x01), self.state)
        handle_uds_request(_block_request(0x02), self.state)
        before = self.snapshot()

        # 回退到更早的 BSC（非 expected-1 的重复）→ 73
        response = handle_uds_request(_block_request(0x01), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA,
                   NRC_WRONG_BLOCK_SEQUENCE_COUNTER]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_data_bearing_request_rejected_in_upload(self) -> None:
        """upload 合法形态 = 严格 2 字节；带 data → 13。"""
        self.start_upload(size=130)
        before = self.snapshot()

        response = handle_uds_request(_block_request(0x01, b"\xAA"), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_before_upload_36_is_24(self) -> None:
        before = self.snapshot()

        response = handle_uds_request(_block_request(0x01), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 6. 32130 boundary（255 块 / FF→00）
# ----------------------------------------------------------------------


class TestUpload32130Boundary(UploadTestCase):

    def _send_all_blocks(self) -> list:
        """发送 BSC 01..FF 共 255 个最大块，返回全部响应。"""
        responses = []

        for counter in range(1, 256):
            request = _block_request(counter & 0xFF)
            responses.append(handle_uds_request(request, self.state))
            self.assertEqual(self.state.upload_expected_block,
                             (counter + 1) & 0xFF)

        return responses

    def test_255_blocks_fill_exactly(self) -> None:
        self.start_upload(size=MAX_UPLOAD_SIZE)

        self._send_all_blocks()

        self.assertEqual(self.state.upload_sent, MAX_UPLOAD_SIZE)
        self.assertEqual(self.state.upload_expected_block, 0x00)

    def test_36_00_after_ff_is_conditions_not_correct(self) -> None:
        """FF 后 expected=0x00：36 00 走新块路径，remaining==0 → 22。"""
        self.start_upload(size=MAX_UPLOAD_SIZE)

        self._send_all_blocks()
        before = self.snapshot()

        response = handle_uds_request(_block_request(0x00), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_ff_after_full_resent(self) -> None:
        """sent==size 时 duplicate BSC FF → 重发最后一块（126 字节）。"""
        self.start_upload(size=MAX_UPLOAD_SIZE)

        self._send_all_blocks()
        before = self.snapshot()

        response = handle_uds_request(_block_request(0xFF), self.state)

        self.assertEqual(
            response,
            bytes([0x76, 0xFF]) + _upload_chunk(MAX_UPLOAD_SIZE - 126, 126),
        )
        self.assertEqual(self.snapshot(), before)

    def test_exit_after_255_blocks(self) -> None:
        self.start_upload(size=MAX_UPLOAD_SIZE)

        self._send_all_blocks()

        response = handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]),
                                      self.state)
        self.assertEqual(response, bytes([POS_REQUEST_TRANSFER_EXIT]))


# ----------------------------------------------------------------------
# 7. 0x37 upload
# ----------------------------------------------------------------------


class TestUploadTransferExit(UploadTestCase):

    def test_incomplete_keeps_active(self) -> None:
        self.start_upload(size=130)

        handle_uds_request(_block_request(0x01), self.state)

        response = handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]),
                                      self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_CONDITIONS_NOT_CORRECT]),
        )
        self.assertTrue(self.state.upload_active)

        # 继续传完再成功退出
        handle_uds_request(_block_request(0x02), self.state)
        self.assertEqual(
            handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state),
            bytes([POS_REQUEST_TRANSFER_EXIT]),
        )

    def test_complete_clears_all_upload_fields(self) -> None:
        self.start_upload(size=130)

        handle_uds_request(_block_request(0x01), self.state)
        handle_uds_request(_block_request(0x02), self.state)

        response = handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]),
                                      self.state)

        self.assertEqual(response, bytes([POS_REQUEST_TRANSFER_EXIT]))
        self.assertFalse(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.upload_size, 0)
        self.assertEqual(self.state.upload_expected_block, 0)
        self.assertEqual(self.state.upload_sent, 0)

    def test_after_exit_36_and_37_are_24(self) -> None:
        self.start_upload(size=4)

        handle_uds_request(_block_request(0x01), self.state)
        handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state)

        self.assertEqual(
            handle_uds_request(_block_request(0x01), self.state),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )
        self.assertEqual(
            handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_REQUEST_SEQUENCE_ERROR]),
        )


# ----------------------------------------------------------------------
# 8. precedence（方向分流 NRC 优先级）
# ----------------------------------------------------------------------


class TestUploadPrecedence(UploadTestCase):

    def test_no_active_36_any_shape_is_24(self) -> None:
        """无 active：任意形态 0x36 → 24，不执行方向长度检查。"""
        for request in (bytes([0x36, 0x01]),
                        bytes([0x36, 0x01, 0xAA]),
                        bytes([0x36, 0x01]) + bytes(130)):
            with self.subTest(length=len(request)):
                response = handle_uds_request(request, self.state)
                self.assertEqual(
                    response,
                    bytes([0x7F, SID_TRANSFER_DATA,
                           NRC_REQUEST_SEQUENCE_ERROR]),
                )

    def test_upload_active_36_three_bytes_is_13(self) -> None:
        """upload active：方向长度规则（严格 2 字节）→ 13。"""
        self.start_upload(size=130)

        response = handle_uds_request(
            bytes([0x36, 0x01, 0xAA]), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_download_active_36_two_bytes_is_13(self) -> None:
        """download active：既有 download 长度规则（3..128）→ 13。"""
        handle_uds_request(_download(), self.state)

        response = handle_uds_request(bytes([0x36, 0x01]), self.state)

        self.assertEqual(
            response,
            bytes([0x7F, SID_TRANSFER_DATA, NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_no_active_37_one_byte_is_24(self) -> None:
        self.assertEqual(
            handle_uds_request(bytes([SID_REQUEST_TRANSFER_EXIT]), self.state),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_no_active_37_two_bytes_is_13(self) -> None:
        """0x37 长度规则方向无关，置于分流之前。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_REQUEST_TRANSFER_EXIT, 0x00]), self.state),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT,
                   NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_upload_active_37_two_bytes_is_13(self) -> None:
        self.start_upload(size=130)

        self.assertEqual(
            handle_uds_request(
                bytes([SID_REQUEST_TRANSFER_EXIT, 0x00]), self.state),
            bytes([0x7F, SID_REQUEST_TRANSFER_EXIT,
                   NRC_INCORRECT_MESSAGE_LENGTH]),
        )


# ----------------------------------------------------------------------
# 9. lifecycle
# ----------------------------------------------------------------------


class TestUploadLifecycle(UploadTestCase):

    def test_hard_reset_clears_upload_five_fields(self) -> None:
        """hardReset 清 upload 5 字段；routine 清、DTC 保留成对断言。

        download 方向的清空不在本测试：download 与 upload 互斥，无法
        同时 active，其复位语义由 test_transfer_unit 成对覆盖。
        """
        self.start_upload(size=130)
        handle_uds_request(_block_request(0x01), self.state)
        # 制造 routine / DTC 状态做成对断言
        handle_uds_request(bytes([0x31, 0x01, 0x20, 0x01]), self.state)
        self.state.dtc_records[0x100009] = 0x09
        dtc_before = dict(self.state.dtc_records)

        handle_uds_request(bytes([0x11, 0x01]), self.state)

        # upload：清（5 字段）
        self.assertFalse(self.state.upload_active)
        self.assertEqual(self.state.upload_address, 0)
        self.assertEqual(self.state.upload_size, 0)
        self.assertEqual(self.state.upload_expected_block, 0)
        self.assertEqual(self.state.upload_sent, 0)
        # routine：清
        self.assertEqual(self.state.active_routines, set())
        self.assertEqual(self.state.routine_start_counts, {})
        # DTC：保留
        self.assertEqual(self.state.dtc_records, dtc_before)
        # 复位后 0x36 回 IDLE
        self.assertEqual(
            handle_uds_request(_block_request(0x01), self.state),
            bytes([0x7F, SID_TRANSFER_DATA, NRC_REQUEST_SEQUENCE_ERROR]),
        )

    def test_session_switch_preserves_upload(self) -> None:
        self.start_upload(size=130)

        handle_uds_request(bytes([0x10, 0x03]), self.state)
        self.assertEqual(self.state.session, 0x03)

        self.assertTrue(self.state.upload_active)
        self.assertEqual(
            handle_uds_request(_block_request(0x01), self.state),
            bytes([0x76, 0x01]) + _upload_chunk(0, 126),
        )

    def test_instance_isolation(self) -> None:
        other = EcuState()

        self.start_upload(size=130)
        handle_uds_request(_block_request(0x01), self.state)

        self.assertFalse(other.upload_active)
        self.assertEqual(other.upload_sent, 0)
        self.assertEqual(other.upload_size, 0)

    def test_mutable_default_isolation(self) -> None:
        first = EcuState()
        second = EcuState()

        first.upload_sent = 5

        self.assertEqual(second.upload_sent, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
