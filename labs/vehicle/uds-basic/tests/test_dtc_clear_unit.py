#!/usr/bin/env python3
"""
UDS Basic —— 0x14 ClearDiagnosticInformation 离线单元测试

直接调用协议层 ``uds_server``，不依赖 vcan0 / SocketCAN / ISO-TP。

覆盖（按已冻结的 Design Review）：

1. 严格 4 字节请求：短请求 / 多余字节 → 0x13
2. ``0xFFFFFF`` all-clear：置零全部 status、**key 集合不变**、空库仍成功
3. 单个 DTC clear：只动目标
4. 未支持 groupOfDTC → 0x31（含 ISO 功能组取值与 0x000000）
5. 任何 NRC 分支都不修改 state
6. 幂等：重复 clear / 已为 0 的 status 再 clear 仍 → 0x54
7. 24-bit big-endian：字节序颠倒 → 0x31（证明不是小端解析）
8. 生命周期：0x14→hardReset 保持已清、hardReset→0x14 正常、实例隔离
9. 静态 ``DTC_DATABASE`` 永不被写入；0x14 不触碰 DID / routine / transfer 状态

明确**不测**（本 PR OUT of SCOPE）：0x85 ControlDTCSetting、0x19 其余
reportType、功能组（functional group）语义、DTC 产生/故障注入机制。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_dtc_clear_unit.py

或：

    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

from dtc_database import DTC_DATABASE  # noqa: E402
from uds_server import (  # noqa: E402
    GROUP_OF_DTC_ALL,
    EcuState,
    handle_uds_request,
)

SID_CLEAR_DTC = 0x14
SID_READ_DTC = 0x19
SID_ECU_RESET = 0x11
SID_REQUEST_DOWNLOAD = 0x34

POS_CLEAR_DTC = 0x54

NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_REQUEST_OUT_OF_RANGE = 0x31

#: 全部 DTC 通配值（与 uds_server.GROUP_OF_DTC_ALL 同值，测试侧独立写出）
GROUP_ALL = 0xFFFFFF

#: 出厂记录（与 dtc_database.DTC_DATABASE 一致，用于 byte-level 断言）
FACTORY = {0x100001: 0x01, 0x100002: 0x02, 0x100003: 0x04}


def clear(group_of_dtc: int) -> bytes:
    """构造 ``14 <groupOfDTC:3>``（24-bit big-endian）。"""
    return bytes([
        SID_CLEAR_DTC,
        (group_of_dtc >> 16) & 0xFF,
        (group_of_dtc >> 8) & 0xFF,
        group_of_dtc & 0xFF,
    ])


def read(report_type: int, payload: bytes = b"") -> bytes:
    return bytes([SID_READ_DTC, report_type]) + payload


def dtc_record(dtc: int, status: int) -> bytes:
    """0x19 响应里的单条记录：DTC_H DTC_M DTC_L statusOfDTC。"""
    return bytes([(dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF, status])


class ClearDtcTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    def setUp(self) -> None:
        self.state = EcuState()

    def assertNegative(self, response: bytes, nrc: int) -> None:
        self.assertEqual(
            response,
            bytes([0x7F, SID_CLEAR_DTC, nrc]),
            f"期望 7F 14 {nrc:02X}，实际 {response.hex(' ')}",
        )

    def snapshot(self) -> dict:
        return dict(self.state.dtc_records)


# ----------------------------------------------------------------------
# 1. 请求长度（严格 4 字节）
# ----------------------------------------------------------------------


class TestClearDtcRequest(ClearDtcTestCase):

    def test_bare_14_is_length_error(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([SID_CLEAR_DTC]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_two_bytes_is_length_error(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([SID_CLEAR_DTC, 0xFF]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_three_bytes_is_length_error(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([SID_CLEAR_DTC, 0xFF, 0xFF]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_extra_byte_is_length_error(self) -> None:
        """多余字节同样是 0x13（用 `!= 4`，与 0x10/0x11/0x31 惯例一致）。"""
        self.assertNegative(
            handle_uds_request(
                bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF, 0x00]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_extra_two_bytes_is_length_error(self) -> None:
        self.assertNegative(
            handle_uds_request(
                bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )


# ----------------------------------------------------------------------
# 2. 0xFFFFFF all-clear
# ----------------------------------------------------------------------


class TestClearDtcAll(ClearDtcTestCase):

    def test_all_clear_exact_response(self) -> None:
        """正响应严格 1 字节 0x54，不回显 groupOfDTC。"""
        response = handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(response, bytes([POS_CLEAR_DTC]))
        self.assertEqual(len(response), 1)

    def test_all_clear_zeroes_every_status(self) -> None:
        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(set(self.state.dtc_records.values()), {0x00})

    def test_all_clear_keeps_key_set(self) -> None:
        """清除 = 置零，**不删 key**：支持集合必须原样保留。"""
        before = set(self.state.dtc_records)

        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(set(self.state.dtc_records), before)
        self.assertEqual(len(self.state.dtc_records), len(FACTORY))

    def test_all_clear_19_01_count_is_zero(self) -> None:
        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(
            handle_uds_request(read(0x01, bytes([0xFF])), self.state),
            bytes([0x59, 0xFF, 0x01, 0x00, 0x00]),
        )

    def test_all_clear_19_02_has_no_matching_record(self) -> None:
        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(
            handle_uds_request(read(0x02, bytes([0xFF])), self.state),
            bytes([0x59, 0xFF]),
        )

    def test_all_clear_19_0a_keeps_members_with_zero_status(self) -> None:
        """0x0A 报"支持的" DTC：成员不变，**只有 status 字节变 0**。"""
        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(
            handle_uds_request(read(0x0A), self.state),
            bytes([0x59, 0xFF])
            + dtc_record(0x100001, 0x00)
            + dtc_record(0x100002, 0x00)
            + dtc_record(0x100003, 0x00),
        )

    def test_group_constant_matches_frozen_profile(self) -> None:
        """本模块常量必须与冻结 profile 的 0xFFFFFF 一致。

        行为断言刻意使用测试侧**独立写出**的 ``GROUP_ALL`` 字面量（而非
        本常量），以便常量被误改时用例会失败；本用例负责钉住两者相等，
        使"独立字面量"与"模块常量"不会静默分叉。
        """
        self.assertEqual(GROUP_OF_DTC_ALL, GROUP_ALL)
        self.assertEqual(GROUP_OF_DTC_ALL, 0xFFFFFF)


# ----------------------------------------------------------------------
# 3. 单个 DTC clear
# ----------------------------------------------------------------------


class TestClearDtcSingle(ClearDtcTestCase):

    def test_single_clear_exact_response(self) -> None:
        self.assertEqual(
            handle_uds_request(clear(0x100002), self.state),
            bytes([POS_CLEAR_DTC]),
        )

    def test_single_clear_zeroes_only_target(self) -> None:
        handle_uds_request(clear(0x100002), self.state)

        self.assertEqual(
            self.state.dtc_records,
            {0x100001: 0x01, 0x100002: 0x00, 0x100003: 0x04},
        )

    def test_single_clear_keeps_key_set(self) -> None:
        before = set(self.state.dtc_records)

        handle_uds_request(clear(0x100002), self.state)

        self.assertEqual(set(self.state.dtc_records), before)

    def test_single_clear_19_01_target_mask_is_zero(self) -> None:
        handle_uds_request(clear(0x100002), self.state)

        self.assertEqual(
            handle_uds_request(read(0x01, bytes([0x02])), self.state),
            bytes([0x59, 0xFF, 0x01, 0x00, 0x00]),
        )

    def test_single_clear_19_01_full_mask_counts_the_rest(self) -> None:
        handle_uds_request(clear(0x100002), self.state)

        self.assertEqual(
            handle_uds_request(read(0x01, bytes([0xFF])), self.state),
            bytes([0x59, 0xFF, 0x01, 0x00, 0x02]),
        )

    def test_single_clear_19_02_excludes_target(self) -> None:
        handle_uds_request(clear(0x100002), self.state)

        self.assertEqual(
            handle_uds_request(read(0x02, bytes([0xFF])), self.state),
            bytes([0x59, 0xFF])
            + dtc_record(0x100001, 0x01)
            + dtc_record(0x100003, 0x04),
        )


# ----------------------------------------------------------------------
# 4. 未支持 groupOfDTC → 0x31
# ----------------------------------------------------------------------


class TestClearDtcUnsupported(ClearDtcTestCase):

    def test_unknown_dtc_is_out_of_range(self) -> None:
        self.assertNegative(
            handle_uds_request(clear(0x100009), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )

    def test_functional_group_ffff00_is_out_of_range(self) -> None:
        """功能组 OUT OF SCOPE：自然落入 0x31（本项目 profile）。"""
        self.assertNegative(
            handle_uds_request(clear(0xFFFF00), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )

    def test_functional_group_fffffe_is_out_of_range(self) -> None:
        self.assertNegative(
            handle_uds_request(clear(0xFFFFFE), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )

    def test_zero_group_is_out_of_range(self) -> None:
        self.assertNegative(
            handle_uds_request(clear(0x000000), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )

    def test_all_group_is_the_supported_control(self) -> None:
        """对照：0xFFFFFF 走成功路径，证明上面的拒绝不是"一律拒绝"。"""
        self.assertEqual(
            handle_uds_request(clear(GROUP_ALL), self.state),
            bytes([POS_CLEAR_DTC]),
        )


# ----------------------------------------------------------------------
# 5. NRC 分支不得修改 state
# ----------------------------------------------------------------------


class TestClearDtcNrcNoMutation(ClearDtcTestCase):

    def test_length_error_does_not_mutate(self) -> None:
        before = self.snapshot()

        handle_uds_request(bytes([SID_CLEAR_DTC]), self.state)
        handle_uds_request(bytes([SID_CLEAR_DTC, 0xFF, 0xFF]), self.state)
        handle_uds_request(
            bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF, 0x00]), self.state)

        self.assertEqual(self.snapshot(), before)

    def test_unsupported_group_does_not_mutate(self) -> None:
        before = self.snapshot()

        handle_uds_request(clear(0x100009), self.state)
        handle_uds_request(clear(0xFFFF00), self.state)
        handle_uds_request(clear(0x000000), self.state)

        self.assertEqual(self.snapshot(), before)

    def test_mixed_nrc_sequence_does_not_mutate(self) -> None:
        before = self.snapshot()

        for request in (bytes([SID_CLEAR_DTC]),
                        clear(0xFFFFFE),
                        bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF, 0x00]),
                        clear(0x123456)):
            handle_uds_request(request, self.state)

        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 6. 幂等
# ----------------------------------------------------------------------


class TestClearDtcIdempotent(ClearDtcTestCase):

    def test_repeat_single_clear_is_54(self) -> None:
        first = handle_uds_request(clear(0x100001), self.state)
        after_first = self.snapshot()

        second = handle_uds_request(clear(0x100001), self.state)

        self.assertEqual(first, bytes([POS_CLEAR_DTC]))
        self.assertEqual(second, bytes([POS_CLEAR_DTC]))
        self.assertEqual(self.snapshot(), after_first)

    def test_repeat_all_clear_is_54(self) -> None:
        handle_uds_request(clear(GROUP_ALL), self.state)
        after_first = self.snapshot()

        for _ in range(3):
            self.assertEqual(
                handle_uds_request(clear(GROUP_ALL), self.state),
                bytes([POS_CLEAR_DTC]),
            )
            self.assertEqual(self.snapshot(), after_first)

    def test_clear_already_zero_status_is_noop_success(self) -> None:
        """已是 0x00 的 status 再清 → 54，且不影响其它条目。"""
        self.state.dtc_records[0x100002] = 0x00
        before = self.snapshot()

        self.assertEqual(
            handle_uds_request(clear(0x100002), self.state),
            bytes([POS_CLEAR_DTC]),
        )
        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 7. 24-bit big-endian
# ----------------------------------------------------------------------


class TestClearDtcByteOrder(ClearDtcTestCase):

    def test_big_endian_hits_expected_dtc(self) -> None:
        handle_uds_request(clear(0x100001), self.state)

        self.assertEqual(
            self.state.dtc_records,
            {0x100001: 0x00, 0x100002: 0x02, 0x100003: 0x04},
        )

    def test_reversed_byte_order_is_unknown(self) -> None:
        """``00 01 10`` 若按小端解析会命中 0x100001 —— 必须 0x31。"""
        before = self.snapshot()

        self.assertNegative(
            handle_uds_request(bytes([SID_CLEAR_DTC, 0x00, 0x01, 0x10]), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )
        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 8. 空库
# ----------------------------------------------------------------------


class TestClearDtcEmptyStore(ClearDtcTestCase):

    def test_all_clear_on_empty_store_is_54_and_stays_empty(self) -> None:
        self.state.dtc_records.clear()

        self.assertEqual(
            handle_uds_request(clear(GROUP_ALL), self.state),
            bytes([POS_CLEAR_DTC]),
        )
        self.assertEqual(self.state.dtc_records, {})

    def test_single_clear_on_empty_store_is_31(self) -> None:
        self.state.dtc_records.clear()

        self.assertNegative(
            handle_uds_request(clear(0x100001), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )


# ----------------------------------------------------------------------
# 9. 生命周期
# ----------------------------------------------------------------------


class TestClearDtcLifecycle(ClearDtcTestCase):

    def test_clear_then_hard_reset_keeps_cleared(self) -> None:
        """hardReset **保留** DTC 记录内容 —— 因此已清除的 status 不回升。"""
        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(
            handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state),
            bytes([0x51, 0x01]),
        )

        self.assertEqual(set(self.state.dtc_records.values()), {0x00})
        self.assertEqual(
            handle_uds_request(read(0x0A), self.state),
            bytes([0x59, 0xFF])
            + dtc_record(0x100001, 0x00)
            + dtc_record(0x100002, 0x00)
            + dtc_record(0x100003, 0x00),
        )

    def test_hard_reset_then_clear_is_normal(self) -> None:
        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        # 复位不清 DTC，因此 still 出厂 status
        self.assertEqual(self.state.dtc_records, FACTORY)

        self.assertEqual(
            handle_uds_request(clear(GROUP_ALL), self.state),
            bytes([POS_CLEAR_DTC]),
        )
        self.assertEqual(set(self.state.dtc_records.values()), {0x00})

    def test_instance_isolation(self) -> None:
        other = EcuState()

        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(other.dtc_records, FACTORY)
        self.assertIsNot(self.state.dtc_records, other.dtc_records)

    def test_static_database_never_modified(self) -> None:
        before = dict(DTC_DATABASE)

        handle_uds_request(clear(GROUP_ALL), self.state)
        handle_uds_request(clear(0x100001), self.state)
        handle_uds_request(bytes([SID_ECU_RESET, 0x01]), self.state)

        self.assertEqual(DTC_DATABASE, before)
        self.assertIsNot(self.state.dtc_records, DTC_DATABASE)


# ----------------------------------------------------------------------
# 10. 0x14 只动 dtc_records
# ----------------------------------------------------------------------


class TestClearDtcIsolation(ClearDtcTestCase):

    def test_clear_does_not_touch_did_overrides_and_routines(self) -> None:
        self.state.did_overrides[0xF102] = b"X"
        self.state.active_routines.add(0x2001)
        self.state.routine_start_counts[0x2001] = 3

        handle_uds_request(clear(GROUP_ALL), self.state)

        self.assertEqual(self.state.did_overrides, {0xF102: b"X"})
        self.assertEqual(self.state.active_routines, {0x2001})
        self.assertEqual(self.state.routine_start_counts, {0x2001: 3})

    def test_clear_does_not_touch_transfer_state(self) -> None:
        """0x14 无 transfer 门控，也不得改动进行中的 transfer 字段。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_REQUEST_DOWNLOAD, 0x00, 0x23, 0x11, 0x22, 0x33, 0x00, 0x04]),
                self.state),
            bytes([0x74, 0x20, 0x00, 0x80]),
        )
        before = (
            self.state.download_active,
            self.state.download_address,
            self.state.download_size,
            self.state.download_expected_block,
            bytes(self.state.download_buffer),
            self.state.download_last_block_request,
        )

        self.assertEqual(
            handle_uds_request(clear(GROUP_ALL), self.state),
            bytes([POS_CLEAR_DTC]),
        )

        self.assertEqual(
            (
                self.state.download_active,
                self.state.download_address,
                self.state.download_size,
                self.state.download_expected_block,
                bytes(self.state.download_buffer),
                self.state.download_last_block_request,
            ),
            before,
        )
        self.assertEqual(set(self.state.dtc_records.values()), {0x00})


if __name__ == "__main__":
    unittest.main(verbosity=2)
