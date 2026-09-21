#!/usr/bin/env python3
"""
UDS Basic —— 0x19 ReadDTCInformation 离线单元测试

直接调用协议层 ``uds_server``，不依赖 vcan0 / SocketCAN / ISO-TP。

覆盖（本 feature scope）:

1. DTC 静态数据库约束（3 字节 DTC / 1 字节 status / 非空）
2. 0x19/01 reportNumberOfDTCByStatusMask（mask 过滤、DTCCount 16-bit BE）
3. 0x19/02 reportDTCByStatusMask（过滤、稳定排序、byte-level exact、无 DTCCount）
4. 0x19/0A reportSupportedDTC（全部 DTC、稳定排序、byte-level exact）
5. NRC：非法 reportType → 0x12；长度错误 → 0x13
6. 运行时状态：连续请求一致；修改 state.dtc_records 立即反映；
   各 EcuState 实例 dtc_records 相互独立（复制而非共享）
7. 未实现 reportType（04/06 等）→ 0x12（显式覆盖，防止误入默认成功路径）

明确**不测**：其他 0x19 reportType（03/04/05/06/07/08/09/0B…）、
suppressPosRspMsgIndicationBit（不在本 feature 范围）。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_dtc_unit.py

或：

    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

from dtc_database import (  # noqa: E402
    DTC_DATABASE,
    DTC_FORMAT_IDENTIFIER,
    DTC_STATUS_AVAILABILITY_MASK,
)
from uds_server import EcuState, handle_uds_request  # noqa: E402

SID_READ_DTC = 0x19
POS_READ_DTC = 0x59

NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13


class DtcTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    def setUp(self) -> None:
        self.state = EcuState()

    def assertNegative(self, response: bytes, nrc: int) -> None:
        self.assertEqual(
            response,
            bytes([0x7F, SID_READ_DTC, nrc]),
            f"期望 7F 19 {nrc:02X}，实际 {response.hex(' ')}",
        )


# ----------------------------------------------------------------------
# 1. 静态数据库约束
# ----------------------------------------------------------------------


class TestDtcDatabase(DtcTestCase):

    def test_database_is_not_empty(self) -> None:
        self.assertTrue(DTC_DATABASE, "DTC_DATABASE 不能为空")

    def test_dtc_values_are_three_bytes(self) -> None:
        for dtc in DTC_DATABASE:
            with self.subTest(dtc=dtc):
                self.assertGreaterEqual(dtc, 0x000000)
                self.assertLessEqual(dtc, 0xFFFFFF)

    def test_status_values_are_one_byte(self) -> None:
        for status in DTC_DATABASE.values():
            with self.subTest(status=status):
                self.assertGreaterEqual(status, 0x00)
                self.assertLessEqual(status, 0xFF)

    def test_statuses_are_distinct_for_mask_filtering(self) -> None:
        """默认状态各不相同，让 mask 过滤具备可断言的教学意义。"""
        statuses = list(DTC_DATABASE.values())
        self.assertEqual(len(statuses), len(set(statuses)))

    def test_default_records_copy_not_shared(self) -> None:
        """dtc_records 必须是 DTC_DATABASE 的复制，不是同一对象。"""
        self.assertIsNot(self.state.dtc_records, DTC_DATABASE)
        self.assertEqual(self.state.dtc_records, DTC_DATABASE)

    def test_instances_have_independent_records(self) -> None:
        """修改一个实例的 dtc_records 不影响另一个实例。"""
        other = EcuState()

        self.state.dtc_records[0x100009] = 0x09

        self.assertNotIn(0x100009, other.dtc_records)


# ----------------------------------------------------------------------
# 2. 0x19/01 reportNumberOfDTCByStatusMask
# ----------------------------------------------------------------------


class TestReportNumberOfDTC(DtcTestCase):

    def test_count_zero_with_empty_mask(self) -> None:
        """mask=0x00：任何 status 都不匹配 → DTCCount=0。"""
        response = handle_uds_request(bytes([0x19, 0x01, 0x00]), self.state)

        self.assertEqual(
            response,
            bytes([0x59, DTC_STATUS_AVAILABILITY_MASK,
                   DTC_FORMAT_IDENTIFIER, 0x00, 0x00]),
        )

    def test_count_zero_with_nonmatching_mask(self) -> None:
        """mask=0x80：默认 status(1/2/4) 均不匹配 → DTCCount=0。"""
        response = handle_uds_request(bytes([0x19, 0x01, 0x80]), self.state)

        self.assertEqual(response, bytes([0x59, 0xFF, 0x01, 0x00, 0x00]))

    def test_count_one_with_bit_mask(self) -> None:
        """mask=0x01：只有 status 含 bit0 的 DTC 匹配。"""
        response = handle_uds_request(bytes([0x19, 0x01, 0x01]), self.state)

        self.assertEqual(response, bytes([0x59, 0xFF, 0x01, 0x00, 0x01]))

    def test_count_all_with_full_mask(self) -> None:
        """mask=0xFF：全部 DTC 匹配 → DTCCount = len(DTC_DATABASE)。"""
        response = handle_uds_request(bytes([0x19, 0x01, 0xFF]), self.state)

        self.assertEqual(
            response,
            bytes([0x59, 0xFF, 0x01,
                   (len(DTC_DATABASE) >> 8) & 0xFF, len(DTC_DATABASE) & 0xFF]),
        )

    def test_count_is_16bit_big_endian(self) -> None:
        """DTCCount 编码为 16-bit big-endian（高字节在前）。"""
        big_count = 0x0100  # 超过 255，必须正确分到两个字节

        self.state.dtc_records.clear()

        for i in range(big_count):
            self.state.dtc_records[0x100000 + i] = 0x01

        response = handle_uds_request(bytes([0x19, 0x01, 0x01]), self.state)

        self.assertEqual(response[-2:], bytes([0x01, 0x00]))
        # 59 <mask> <format> <countH> <countL> = 5 字节
        self.assertEqual(len(response), 5, "0x19/01 响应固定 5 字节")

    def test_count_reflects_runtime_state(self) -> None:
        """修改 dtc_records 后 0x19/01 立即反映运行时状态。"""
        self.state.dtc_records.clear()
        self.state.dtc_records[0x10000A] = 0x02

        response = handle_uds_request(bytes([0x19, 0x01, 0x02]), self.state)

        self.assertEqual(response[-2:], bytes([0x00, 0x01]))


# ----------------------------------------------------------------------
# 3. 0x19/02 reportDTCByStatusMask
# ----------------------------------------------------------------------


class TestReportDTCByStatusMask(DtcTestCase):

    def test_returns_all_matching_dtcs_byte_exact(self) -> None:
        """mask=0xFF → 全部 DTC，按 DTC 升序，byte-level exact。

        byte-level exact 同时固化了"0x19/02 不含 DTCCount"——响应
        中只有 availability mask + 4 字节/条的 DTC 记录。
        """
        response = handle_uds_request(bytes([0x19, 0x02, 0xFF]), self.state)

        expected = bytes([0x59, 0xFF])

        for dtc in sorted(DTC_DATABASE):
            status = DTC_DATABASE[dtc]
            expected += bytes([
                (dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF, status,
            ])

        self.assertEqual(response, expected)

    def test_filters_nonmatching_dtcs(self) -> None:
        """mask=0x01 → 只返回 status bit0 置位的 DTC。"""
        response = handle_uds_request(bytes([0x19, 0x02, 0x01]), self.state)

        self.assertEqual(
            response,
            bytes([0x59, 0xFF, 0x10, 0x00, 0x01, 0x01]),
        )

    def test_empty_result_when_nothing_matches(self) -> None:
        """mask=0x80 → 无匹配 → 只返回 availability mask。"""
        response = handle_uds_request(bytes([0x19, 0x02, 0x80]), self.state)

        self.assertEqual(response, bytes([0x59, 0xFF]))

    def test_output_order_is_stable_by_dtc(self) -> None:
        """DTC 顺序稳定：按 DTC 升序，与插入顺序无关。"""
        self.state.dtc_records.clear()
        self.state.dtc_records[0x300003] = 0x01
        self.state.dtc_records[0x100001] = 0x01
        self.state.dtc_records[0x200002] = 0x01

        first = handle_uds_request(bytes([0x19, 0x02, 0x01]), self.state)
        second = handle_uds_request(bytes([0x19, 0x02, 0x01]), self.state)

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            bytes([0x59, 0xFF,
                   0x10, 0x00, 0x01, 0x01,
                   0x20, 0x00, 0x02, 0x01,
                   0x30, 0x00, 0x03, 0x01]),
        )

    def test_response_reflects_runtime_state(self) -> None:
        """修改 dtc_records 后 0x19/02 立即反映运行时状态。"""
        self.state.dtc_records.clear()
        self.state.dtc_records[0x100009] = 0x04

        response = handle_uds_request(bytes([0x19, 0x02, 0x04]), self.state)

        self.assertEqual(
            response,
            bytes([0x59, 0xFF, 0x10, 0x00, 0x09, 0x04]),
        )


# ----------------------------------------------------------------------
# 4. 0x19/0A reportSupportedDTC
# ----------------------------------------------------------------------


class TestReportSupportedDTC(DtcTestCase):

    def test_reports_all_supported_dtcs_byte_exact(self) -> None:
        response = handle_uds_request(bytes([0x19, 0x0A]), self.state)

        expected = bytes([0x59, 0xFF])

        for dtc in sorted(DTC_DATABASE):
            status = DTC_DATABASE[dtc]
            expected += bytes([
                (dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF, status,
            ])

        self.assertEqual(response, expected)

    def test_order_is_stable(self) -> None:
        first = handle_uds_request(bytes([0x19, 0x0A]), self.state)
        second = handle_uds_request(bytes([0x19, 0x0A]), self.state)

        self.assertEqual(first, second)

    def test_reports_runtime_zero_status_dtc(self) -> None:
        """0x0A 报告"支持的" DTC，不受 status 过滤——status=0 也出现。"""
        self.state.dtc_records[0x100009] = 0x00

        response = handle_uds_request(bytes([0x19, 0x0A]), self.state)

        self.assertIn(bytes([0x10, 0x00, 0x09, 0x00]), response)


# ----------------------------------------------------------------------
# 5. NRC：非法 reportType / 长度错误
# ----------------------------------------------------------------------


class TestDtcNrc(DtcTestCase):

    def test_unknown_report_types_rejected(self) -> None:
        for report_type in (0x03, 0x7F, 0x04, 0x06, 0x0B, 0xFF):
            with self.subTest(report_type=report_type):
                response = handle_uds_request(
                    bytes([0x19, report_type]), self.state
                )
                self.assertNegative(response, NRC_SUBFUNCTION_NOT_SUPPORTED)

    def test_bare_19_too_short(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([0x19]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_01_missing_mask(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([0x19, 0x01]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_01_extra_data(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([0x19, 0x01, 0xFF, 0x00]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_02_missing_mask(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([0x19, 0x02]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_02_extra_data(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([0x19, 0x02, 0xFF, 0x00]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_0a_extra_data(self) -> None:
        self.assertNegative(
            handle_uds_request(bytes([0x19, 0x0A, 0x00]), self.state),
            NRC_INCORRECT_MESSAGE_LENGTH,
        )

    def test_nrc_does_not_mutate_state(self) -> None:
        """否定响应不得改变 DTC 运行时状态。"""
        before = dict(self.state.dtc_records)

        handle_uds_request(bytes([0x19, 0x03]), self.state)
        handle_uds_request(bytes([0x19, 0x01, 0xFF, 0x00]), self.state)

        self.assertEqual(self.state.dtc_records, before)


# ----------------------------------------------------------------------
# 6. 运行时状态：跨请求一致 + 读取不改变状态
# ----------------------------------------------------------------------


class TestDtcStatePersistence(DtcTestCase):

    def test_consecutive_requests_see_same_state(self) -> None:
        """连续两个相同请求必须看到相同的 DTC 状态。"""
        first = handle_uds_request(bytes([0x19, 0x02, 0xFF]), self.state)
        second = handle_uds_request(bytes([0x19, 0x02, 0xFF]), self.state)

        self.assertEqual(first, second)

    def test_reads_do_not_mutate_records(self) -> None:
        """0x19/01、02、0A 都是只读查询，不得修改 dtc_records。"""
        before = dict(self.state.dtc_records)

        handle_uds_request(bytes([0x19, 0x01, 0xFF]), self.state)
        handle_uds_request(bytes([0x19, 0x02, 0x01]), self.state)
        handle_uds_request(bytes([0x19, 0x0A]), self.state)

        self.assertEqual(self.state.dtc_records, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
