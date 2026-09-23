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
7. 未实现 reportType（03/04/05/06/0B 等）→ 0x12（显式覆盖，防止误入默认成功路径）

OQ5 severity 家族（本文件下半部分）:

8. DTC_METADATA 完整性（键集合 == DTC_DATABASE，导入期护栏的可测对应物）
9. 0x19/07 reportNumberOfDTCBySeverityMaskRecord（双 mask 逻辑与、16-bit BE）
10. 0x19/08 reportDTCBySeverityMaskRecord（每条记录 6 字节、稳定排序）
11. 0x19/09 reportSeverityInformationOfDTC（已知/未知 DTC、status 取自运行时）
12. severity 与 0x14 的**正交性**（清 status 不改 severity）
13. severity 与 ECUReset 的一致性（0x11 不清 DTC、不改 metadata）
14. **wire profile 冻结**（01/02/0A/07/08/09 一致），两条约定：
    * 正响应**不回显 reportType**；
    * count 型正响应**不输出 DTCSeverityAvailabilityMask**，
      记录型正响应的字段顺序为 **severity → functionalUnit → DTC → status**。

wire format（SecurityLab profile）::

    19 07 E0 FF  → 59 FF 01 00 03                     （5 字节，同 0x19/01 形状）
    19 08 E0 FF  → 59 FF 80 01 10 00 01 01 40 02 …    （20 字节，走 ISO-TP MF）
    19 09 10 00 01 → 59 FF 80 01 10 00 01 01          （8 字节）

明确**不测**：其他 0x19 reportType（03/04/05/06/0B–0E/14/15/1A…）、
suppressPosRspMsgIndicationBit（不在本 feature 范围）。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_dtc_unit.py

或：

    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import dtc_runtime  # noqa: E402
import uds_server  # noqa: E402
from dtc_database import (  # noqa: E402
    DTC_DATABASE,
    DTC_FORMAT_IDENTIFIER,
    DTC_METADATA,
    DTC_SEVERITY_AVAILABILITY_MASK,
    DTC_SEVERITY_CHECK_AT_NEXT_HALT,
    DTC_SEVERITY_CHECK_IMMEDIATELY,
    DTC_SEVERITY_MAINTENANCE_ONLY,
    DTC_STATUS_AVAILABILITY_MASK,
    DtcMetadata,
)
from uds_server import EcuState, handle_uds_request  # noqa: E402

SID_READ_DTC = 0x19
SID_CLEAR_DTC = 0x14
SID_ECU_RESET = 0x11
POS_READ_DTC = 0x59

NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_REQUEST_OUT_OF_RANGE = 0x31

GROUP_OF_DTC_ALL = 0xFFFFFF

#: 本实验采用的三个 severity 位（各自恰好选中一个 DTC —— 见 metadata 设计）。
ALL_SEVERITY_BITS = (
    DTC_SEVERITY_CHECK_IMMEDIATELY,
    DTC_SEVERITY_CHECK_AT_NEXT_HALT,
    DTC_SEVERITY_MAINTENANCE_ONLY,
)


class DtcTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    #: 0x19/09 正响应布局（SecurityLab profile）：
    #: ``59 <statusAvailMask> <severity:1> <functionalUnit:1> <DTC:3> <status:1>``
    #:
    #: 两条 profile 约定：
    #:   * **不回显 reportType**（见 TestReadDtcWireProfile）
    #:   * **不输出 DTCSeverityAvailabilityMask**
    SEV_INFO_SEVERITY = 2
    SEV_INFO_FUNCTIONAL_UNIT = 3
    SEV_INFO_DTC_SLICE = slice(4, 7)
    SEV_INFO_STATUS = 7

    def setUp(self) -> None:
        self.state = EcuState()

    def assertNegative(self, response: bytes, nrc: int) -> None:
        self.assertEqual(
            response,
            bytes([0x7F, SID_READ_DTC, nrc]),
            f"期望 7F 19 {nrc:02X}，实际 {response.hex(' ')}",
        )

    def parse_severity_info(self, response: bytes) -> dict:
        """把 0x19/09 正响应解析为具名字段，避免裸下标写错偏移。"""
        self.assertEqual(
            response[0], POS_READ_DTC,
            f"期望 0x19/09 肯定响应，实际 {response.hex(' ')}",
        )
        self.assertEqual(
            response[1], DTC_STATUS_AVAILABILITY_MASK,
            "第 2 字节必须是 DTCStatusAvailabilityMask",
        )

        return {
            "severity": response[self.SEV_INFO_SEVERITY],
            "functional_unit": response[self.SEV_INFO_FUNCTIONAL_UNIT],
            "dtc": int.from_bytes(response[self.SEV_INFO_DTC_SLICE], "big"),
            "status": response[self.SEV_INFO_STATUS],
        }


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


# ======================================================================
# OQ5：DTC severity 家族（0x19/07、08、09）
# ======================================================================


class TestDtcSeverityMetadata(DtcTestCase):
    """DTC_METADATA 静态定义约束（导入期护栏的可测对应物）。"""

    def test_covers_exactly_the_supported_dtcs(self) -> None:
        """每个支持的 DTC 都必须有 metadata，且不得有多余条目。"""
        self.assertEqual(set(DTC_DATABASE), set(DTC_METADATA))

    def test_every_severity_is_a_known_severity_bit(self) -> None:
        for dtc, metadata in DTC_METADATA.items():
            with self.subTest(dtc=hex(dtc)):
                self.assertIn(
                    metadata.severity, ALL_SEVERITY_BITS,
                    f"0x{dtc:06X} 的 severity 0x{metadata.severity:02X} "
                    f"不是本实验定义的 severity 位",
                )

    def test_severities_are_distinct(self) -> None:
        """三个 severity 各不相同 —— "每个 mask 恰好选中一个 DTC" 的前提。"""
        severities = [m.severity for m in DTC_METADATA.values()]

        self.assertEqual(len(severities), len(set(severities)))

    def test_functional_units_are_single_byte(self) -> None:
        for dtc, metadata in DTC_METADATA.items():
            with self.subTest(dtc=hex(dtc)):
                self.assertTrue(0x00 <= metadata.functional_unit <= 0xFF)

    def test_severity_availability_mask_matches_the_three_bits(self) -> None:
        self.assertEqual(DTC_SEVERITY_AVAILABILITY_MASK, 0xE0)

        for bit in ALL_SEVERITY_BITS:
            with self.subTest(bit=hex(bit)):
                self.assertEqual(DTC_SEVERITY_AVAILABILITY_MASK & bit, bit)

    def test_metadata_is_immutable(self) -> None:
        """DtcMetadata 是 NamedTuple：运行时不得被改写。"""
        metadata = DTC_METADATA[0x100001]

        with self.assertRaises(AttributeError):
            metadata.severity = 0x00  # type: ignore[misc]

    def test_metadata_is_not_copied_into_runtime_state(self) -> None:
        """severity 不得混进 EcuState.dtc_records（那里只放 status）。"""
        state = EcuState()

        self.assertEqual(set(state.dtc_records), set(DTC_DATABASE))

        for value in state.dtc_records.values():
            self.assertIsInstance(value, int)


class TestReportNumberOfDTCBySeverity(DtcTestCase):
    """0x19/07 reportNumberOfDTCBySeverityMaskRecord。"""

    def count_of(self, severity_mask: int, status_mask: int) -> int:
        response = handle_uds_request(
            bytes([0x19, 0x07, severity_mask, status_mask]), self.state
        )

        # SecurityLab profile：``59 <statusAvail> <format> <count:2>``，
        # 共 5 字节 —— **没有 DTCSeverityAvailabilityMask**。
        self.assertEqual(
            response[:2] + response[2:3],
            bytes([
                POS_READ_DTC,
                DTC_STATUS_AVAILABILITY_MASK,
                DTC_FORMAT_IDENTIFIER,
            ]),
        )
        self.assertEqual(len(response), 5, "0x19/07 响应固定 5 字节")

        return (response[-2] << 8) | response[-1]

    def test_byte_exact_full_masks(self) -> None:
        response = handle_uds_request(
            bytes([0x19, 0x07, 0xE0, 0xFF]), self.state
        )

        self.assertEqual(response, bytes.fromhex("59 FF 01 00 03"))

    def test_no_severity_availability_byte(self) -> None:
        """
        0x19/07 **不得**输出 DTCSeverityAvailabilityMask。

        本实验的 count 型正响应与 0x19/01 同形（SID + availability +
        format + count）。去掉 reportType 只意味着**少一个字段**，不代表
        可以改变其余字段的存在性。
        """
        for severity_mask in (0xE0, 0x80, 0x40, 0x20, 0x00):
            with self.subTest(severity_mask=hex(severity_mask)):
                response = handle_uds_request(
                    bytes([0x19, 0x07, severity_mask, 0xFF]), self.state
                )

                # 第 3 字节必须是 DTCFormatIdentifier（而非 severity availability）
                self.assertEqual(response[2], DTC_FORMAT_IDENTIFIER)
                self.assertNotEqual(response[2], DTC_SEVERITY_AVAILABILITY_MASK)
                self.assertEqual(len(response), 5)

    def test_shape_matches_0x19_01(self) -> None:
        """0x19/07 与 0x19/01 必须同形（仅计数口径不同）。"""
        seventh = handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state)
        first = handle_uds_request(bytes([0x19, 0x01, 0xFF]), self.state)

        self.assertEqual(len(seventh), len(first))
        self.assertEqual(seventh[:3], first[:3])

    def test_full_masks_count_every_dtc(self) -> None:
        self.assertEqual(self.count_of(0xE0, 0xFF), len(DTC_DATABASE))

    def test_each_severity_bit_selects_exactly_one_dtc(self) -> None:
        """0x80 / 0x40 / 0x20 必须能分别筛选。"""
        for bit in ALL_SEVERITY_BITS:
            with self.subTest(bit=hex(bit)):
                self.assertEqual(self.count_of(bit, 0xFF), 1)

    def test_both_masks_must_match(self) -> None:
        """两个 mask 是逻辑与：任一不满足都必须排除。"""
        # 0x100001: severity 0x80 / status 0x01
        self.assertEqual(self.count_of(0x80, 0x01), 1)
        self.assertEqual(self.count_of(0x80, 0x02), 0, "severity 对但 status 不对")
        self.assertEqual(self.count_of(0x40, 0x01), 0, "status 对但 severity 不对")

    def test_zero_severity_mask_counts_nothing(self) -> None:
        self.assertEqual(self.count_of(0x00, 0xFF), 0)

    def test_zero_status_mask_counts_nothing(self) -> None:
        self.assertEqual(self.count_of(0xE0, 0x00), 0)

    def test_count_is_16bit_big_endian(self) -> None:
        """DTCCount 编码为 16-bit big-endian（高字节在前）。"""
        big_count = 0x0100  # 超过 255，必须正确分到两个字节

        self.state.dtc_records.clear()

        metadata = {
            0x100000 + i: DtcMetadata(DTC_SEVERITY_CHECK_IMMEDIATELY, 0x01)
            for i in range(big_count)
        }

        for dtc in metadata:
            self.state.dtc_records[dtc] = 0x01

        # 注入 DTC 的 metadata 需要临时挂到 uds_server 的命名空间上
        # （handler 读的是它自己 import 的那份引用）。
        with mock.patch.object(uds_server, "DTC_METADATA", metadata):
            response = handle_uds_request(
                bytes([0x19, 0x07, 0x80, 0x01]), self.state
            )

        self.assertEqual(response[-2:], bytes([0x01, 0x00]))
        self.assertEqual(len(response), 5, "0x19/07 响应固定 5 字节")


class TestReportDTCBySeverity(DtcTestCase):
    """0x19/08 reportDTCBySeverityMaskRecord。"""

    #: 每条记录 6 字节，字段顺序 **severity(1) → functionalUnit(1) →
    #: DTC(3) → status(1)**（SecurityLab profile）。
    RECORD_SIZE = 6

    #: 正响应头部长度：``59 <statusAvailMask>``，然后紧跟记录。
    HEADER_SIZE = 2

    def records_of(self, response: bytes) -> list:
        self.assertEqual(
            response[:2],
            bytes([POS_READ_DTC, DTC_STATUS_AVAILABILITY_MASK]),
            "0x19/08 正响应必须以 59 <statusAvailMask> 开头",
        )

        body = response[self.HEADER_SIZE:]

        self.assertEqual(
            len(body) % self.RECORD_SIZE, 0,
            "0x19/08 记录部分必须是 6 字节的整数倍",
        )

        return [
            body[i:i + self.RECORD_SIZE]
            for i in range(0, len(body), self.RECORD_SIZE)
        ]

    def test_full_mask_byte_exact(self) -> None:
        response = handle_uds_request(
            bytes([0x19, 0x08, 0xE0, 0xFF]), self.state
        )

        self.assertEqual(
            response,
            bytes.fromhex(
                "59 FF"
                " 80 01 10 00 01 01"
                " 40 02 10 00 02 02"
                " 20 03 10 00 03 04"
            ),
        )

    def test_no_severity_availability_byte(self) -> None:
        """0x19/08 头部只有 SID + statusAvail，**不含** severity availability。"""
        response = handle_uds_request(
            bytes([0x19, 0x08, 0xE0, 0xFF]), self.state
        )

        self.assertEqual(response[0], POS_READ_DTC)
        self.assertEqual(response[1], DTC_STATUS_AVAILABILITY_MASK)
        self.assertNotEqual(
            response[1], DTC_SEVERITY_AVAILABILITY_MASK,
            "第 2 字节不得是 DTCSeverityAvailabilityMask",
        )

    def test_every_record_is_six_bytes(self) -> None:
        response = handle_uds_request(
            bytes([0x19, 0x08, 0xE0, 0xFF]), self.state
        )

        self.assertEqual(len(self.records_of(response)), len(DTC_DATABASE))
        self.assertEqual(
            len(response),
            self.HEADER_SIZE + self.RECORD_SIZE * len(DTC_DATABASE),
        )
        self.assertEqual(len(response), 20, "3 DTC 时固定 20 字节（>7 → MF）")

    def test_record_field_order(self) -> None:
        """
        字段顺序必须是 **severity → functionalUnit → DTC → status**。

        交叉核对：按上述偏移解读出来的值必须与 DTC_METADATA / dtc_records
        完全对应；若顺序写错，读取值必然对不上。
        """
        response = handle_uds_request(
            bytes([0x19, 0x08, 0xE0, 0xFF]), self.state
        )

        for record in self.records_of(response):
            severity = record[0]
            functional_unit = record[1]
            dtc = int.from_bytes(record[2:5], "big")
            status = record[5]

            metadata = DTC_METADATA[dtc]

            with self.subTest(dtc=hex(dtc)):
                self.assertEqual(severity, metadata.severity, "record[0] 必须是 severity")
                self.assertEqual(
                    functional_unit, metadata.functional_unit,
                    "record[1] 必须是 functionalUnit",
                )
                self.assertEqual(
                    status, self.state.dtc_records[dtc],
                    "record[5] 必须是当前 status",
                )

    def test_records_carry_all_four_fields(self) -> None:
        response = handle_uds_request(
            bytes([0x19, 0x08, 0x80, 0xFF]), self.state
        )

        self.assertEqual(
            self.records_of(response),
            [bytes([0x80, 0x01, 0x10, 0x00, 0x01, 0x01])],
        )

    def test_output_order_is_stable_by_dtc(self) -> None:
        first = handle_uds_request(bytes([0x19, 0x08, 0xE0, 0xFF]), self.state)
        second = handle_uds_request(bytes([0x19, 0x08, 0xE0, 0xFF]), self.state)

        self.assertEqual(first, second)

        dtcs = [
            int.from_bytes(record[2:5], "big")
            for record in self.records_of(first)
        ]

        self.assertEqual(dtcs, sorted(dtcs), "记录必须按 DTC 升序")

    def test_severity_mask_filters_records(self) -> None:
        response = handle_uds_request(
            bytes([0x19, 0x08, 0x20, 0xFF]), self.state
        )

        self.assertEqual(
            self.records_of(response),
            [bytes([0x20, 0x03, 0x10, 0x00, 0x03, 0x04])],
        )

    def test_empty_result_is_header_only(self) -> None:
        response = handle_uds_request(
            bytes([0x19, 0x08, 0x00, 0xFF]), self.state
        )

        self.assertEqual(response, bytes([POS_READ_DTC, DTC_STATUS_AVAILABILITY_MASK]))


class TestReportSeverityInformation(DtcTestCase):
    """0x19/09 reportSeverityInformationOfDTC。"""

    def request_for(self, dtc: int) -> bytes:
        return bytes([
            0x19, 0x09, (dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF,
        ])

    def test_known_dtc_byte_exact(self) -> None:
        response = handle_uds_request(self.request_for(0x100001), self.state)

        self.assertEqual(response, bytes.fromhex("59 FF 80 01 10 00 01 01"))
        self.assertEqual(len(response), 8)

    def test_field_order(self) -> None:
        """
        字段顺序必须是 **severity → functionalUnit → DTC → status**。

        交叉核对：按该偏移解读出的值必须与 `DTC_METADATA` /
        `dtc_records` 完全对应；若顺序写错，读取值必然对不上。
        """
        for dtc in sorted(self.state.dtc_records):
            with self.subTest(dtc=hex(dtc)):
                parsed = self.parse_severity_info(
                    handle_uds_request(self.request_for(dtc), self.state)
                )

                self.assertEqual(parsed["severity"], DTC_METADATA[dtc].severity)
                self.assertEqual(
                    parsed["functional_unit"], DTC_METADATA[dtc].functional_unit
                )
                self.assertEqual(parsed["dtc"], dtc)
                self.assertEqual(parsed["status"], self.state.dtc_records[dtc])

    def test_each_dtc_reports_its_own_metadata(self) -> None:
        expected = {
            0x100001: (0x80, 0x01),
            0x100002: (0x40, 0x02),
            0x100003: (0x20, 0x03),
        }

        for dtc, (severity, functional_unit) in expected.items():
            with self.subTest(dtc=hex(dtc)):
                parsed = self.parse_severity_info(
                    handle_uds_request(self.request_for(dtc), self.state)
                )

                self.assertEqual(parsed["dtc"], dtc)
                self.assertEqual(parsed["severity"], severity)
                self.assertEqual(parsed["functional_unit"], functional_unit)

    def test_status_comes_from_runtime_state(self) -> None:
        self.state.dtc_records[0x100001] = 0x07

        parsed = self.parse_severity_info(
            handle_uds_request(self.request_for(0x100001), self.state)
        )

        self.assertEqual(parsed["status"], 0x07, "status 必须取自 dtc_records")
        self.assertEqual(parsed["severity"], 0x80, "severity 不随 status 改变")

    def test_unknown_dtc_is_out_of_range(self) -> None:
        response = handle_uds_request(self.request_for(0x100009), self.state)

        self.assertNegative(response, NRC_REQUEST_OUT_OF_RANGE)

    def test_cleared_dtc_still_answers(self) -> None:
        """0x14 之后 DTC 仍存在（key 保留），0x09 必须仍能回答它。"""
        handle_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), self.state)

        parsed = self.parse_severity_info(
            handle_uds_request(self.request_for(0x100001), self.state)
        )

        self.assertEqual(parsed["dtc"], 0x100001)
        self.assertEqual(parsed["status"], 0x00)


class TestDtcSeverityUnknownMetadata(DtcTestCase):
    """
    运行时注入的、**无 metadata 声明**的 DTC 必须按 ``noSeverityAvailable``
    处理，而不是抛 KeyError —— ``EcuState.dtc_records`` 允许运行时注入
    （见 ``test_hard_reset_preserves_dtc_records`` 注入 0x100009）。
    """

    UNKNOWN_DTC = 0x100009

    def setUp(self) -> None:
        super().setUp()
        self.state.dtc_records[self.UNKNOWN_DTC] = 0x01

    def test_not_selected_by_any_nonzero_severity_mask(self) -> None:
        response = handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state)

        self.assertEqual(
            (response[-2] << 8) | response[-1], len(DTC_DATABASE),
            "无 metadata 的 DTC severity=0x00，不应被任何非零 mask 选中",
        )

    def test_absent_from_08_records(self) -> None:
        response = handle_uds_request(bytes([0x19, 0x08, 0xE0, 0xFF]), self.state)

        self.assertNotIn(bytes([0x10, 0x00, 0x09]), response)

    def test_09_still_answers_with_no_severity(self) -> None:
        parsed = self.parse_severity_info(
            handle_uds_request(bytes([0x19, 0x09, 0x10, 0x00, 0x09]), self.state)
        )

        self.assertEqual(parsed["dtc"], self.UNKNOWN_DTC)
        self.assertEqual(parsed["status"], 0x01, "status 仍取自运行时")
        self.assertEqual(parsed["severity"], 0x00, "无 metadata → noSeverityAvailable")
        self.assertEqual(parsed["functional_unit"], 0x00)


class TestDtcSeverityClearInteraction(DtcTestCase):
    """severity 是 DTC **定义属性**：0x14 只动 status，绝不动 severity。"""

    def severity_response(self, dtc: int) -> bytes:
        return handle_uds_request(
            bytes([
                0x19, 0x09, (dtc >> 16) & 0xFF, (dtc >> 8) & 0xFF, dtc & 0xFF,
            ]),
            self.state,
        )

    def test_all_clear_zeroes_07_count(self) -> None:
        before = handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state)

        self.assertEqual((before[-2] << 8) | before[-1], len(DTC_DATABASE))

        handle_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), self.state)

        after = handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state)

        self.assertEqual(after[-2:], bytes([0x00, 0x00]))

    def test_all_clear_empties_08_records(self) -> None:
        handle_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), self.state)

        response = handle_uds_request(bytes([0x19, 0x08, 0xE0, 0xFF]), self.state)

        self.assertEqual(
            response,
            bytes([POS_READ_DTC, DTC_STATUS_AVAILABILITY_MASK]),
        )

    def test_all_clear_keeps_09_status_zero_but_severity_intact(self) -> None:
        before = self.parse_severity_info(self.severity_response(0x100001))

        handle_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), self.state)

        after = self.parse_severity_info(self.severity_response(0x100001))

        self.assertEqual(after["dtc"], 0x100001, "DTC 必须仍然存在")
        self.assertEqual(after["status"], 0x00, "status 必须被清为 0x00")
        self.assertEqual(
            after["severity"], before["severity"], "severity 不得随 0x14 改变"
        )
        self.assertEqual(
            after["functional_unit"], before["functional_unit"],
            "functionalUnit 不得随 0x14 改变",
        )

    def test_single_clear_only_affects_that_dtc(self) -> None:
        handle_uds_request(bytes([0x14, 0x10, 0x00, 0x01]), self.state)

        cleared = self.parse_severity_info(self.severity_response(0x100001))
        untouched = self.parse_severity_info(self.severity_response(0x100002))

        self.assertEqual(cleared["status"], 0x00)
        self.assertEqual(untouched["status"], 0x02)
        self.assertEqual(cleared["severity"], DTC_SEVERITY_CHECK_IMMEDIATELY)
        self.assertEqual(untouched["severity"], DTC_SEVERITY_CHECK_AT_NEXT_HALT)

    def test_07_after_single_clear_excludes_only_that_dtc(self) -> None:
        handle_uds_request(bytes([0x14, 0x10, 0x00, 0x01]), self.state)

        # 0x100001 的 status 被清零 → severity 0x80 + status 0x01 不再同时匹配
        response = handle_uds_request(bytes([0x19, 0x07, 0x80, 0x01]), self.state)

        self.assertEqual(response[-2:], bytes([0x00, 0x00]))

    def test_severity_data_is_consistent_across_every_reset_type(self) -> None:
        """0x11 01/02/03 都不清 DTC、不改 metadata —— 结果必须一致。"""
        before07 = handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state)
        before09 = self.severity_response(0x100001)

        for reset_type in (0x01, 0x02, 0x03):
            with self.subTest(reset_type=hex(reset_type)):
                handle_uds_request(
                    bytes([SID_ECU_RESET, reset_type]), self.state
                )

                after07 = handle_uds_request(
                    bytes([0x19, 0x07, 0xE0, 0xFF]), self.state
                )
                after09 = self.severity_response(0x100001)

                self.assertEqual(after07, before07)
                self.assertEqual(after09, before09)


class TestDtcSeverityPurity(DtcTestCase):
    """0x07 / 08 / 09 都是只读查询。"""

    def test_severity_reports_do_not_mutate_state(self) -> None:
        before = dict(self.state.dtc_records)

        handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state)
        handle_uds_request(bytes([0x19, 0x08, 0xE0, 0xFF]), self.state)
        handle_uds_request(bytes([0x19, 0x09, 0x10, 0x00, 0x01]), self.state)

        self.assertEqual(self.state.dtc_records, before)

    def test_repeated_requests_are_stable(self) -> None:
        for request in (
            bytes([0x19, 0x07, 0xE0, 0xFF]),
            bytes([0x19, 0x08, 0xE0, 0xFF]),
            bytes([0x19, 0x09, 0x10, 0x00, 0x01]),
        ):
            with self.subTest(request=request.hex(" ")):
                self.assertEqual(
                    handle_uds_request(request, self.state),
                    handle_uds_request(request, self.state),
                )


class TestDtcSeverityNrc(DtcTestCase):

    def test_length_errors(self) -> None:
        cases = (
            (bytes([0x19, 0x07, 0xE0]), "07 缺 statusMask"),
            (bytes([0x19, 0x07, 0xE0, 0xFF, 0x00]), "07 多余字节"),
            (bytes([0x19, 0x08, 0xE0]), "08 缺 statusMask"),
            (bytes([0x19, 0x08, 0xE0, 0xFF, 0x00]), "08 多余字节"),
            (bytes([0x19, 0x09, 0x10, 0x00]), "09 缺 DTC 最低字节"),
            (bytes([0x19, 0x09, 0x10, 0x00, 0x01, 0x00]), "09 多余字节"),
        )

        for request, label in cases:
            with self.subTest(label=label):
                self.assertNegative(
                    handle_uds_request(request, self.state),
                    NRC_INCORRECT_MESSAGE_LENGTH,
                )

    def test_unimplemented_report_types_still_rejected(self) -> None:
        """本次未实现的 reportType 不得误入成功路径（仍 NRC 0x12）。"""
        for report_type in (0x03, 0x04, 0x05, 0x06,
                            0x0B, 0x0C, 0x0D, 0x0E,
                            0x14, 0x15, 0x1A, 0x7F):
            with self.subTest(report_type=hex(report_type)):
                self.assertNegative(
                    handle_uds_request(bytes([0x19, report_type]), self.state),
                    NRC_SUBFUNCTION_NOT_SUPPORTED,
                )

    def test_nrc_does_not_mutate_state(self) -> None:
        before = dict(self.state.dtc_records)

        handle_uds_request(bytes([0x19, 0x09, 0x10, 0x00, 0x09]), self.state)
        handle_uds_request(bytes([0x19, 0x07, 0xE0]), self.state)

        self.assertEqual(self.state.dtc_records, before)


class TestReadDtcWireProfile(DtcTestCase):
    """
    正响应**不回显 reportType** —— 本实验**既有的 wire contract**。

    ISO 14229-1 的形式是 ``59 <reportType> <availMask> ...``；本实验自
    01/02/0A 起就采用 ``59 <availMask> ...``，并被逐字节固化。OQ5 的
    07/08/09 **沿用同一 profile**，不因"标准如此"回头改既有三个类型，
    也不在新类型上单独引入回显 —— 否则同一服务会出现两套解析约定。
    本测试把该约定钉死为可执行契约。
    """

    def test_status_family_does_not_echo_report_type(self) -> None:
        responses = {
            0x01: handle_uds_request(bytes([0x19, 0x01, 0xFF]), self.state),
            0x02: handle_uds_request(bytes([0x19, 0x02, 0xFF]), self.state),
            0x0A: handle_uds_request(bytes([0x19, 0x0A]), self.state),
        }

        for report_type, response in responses.items():
            with self.subTest(report_type=hex(report_type)):
                self.assertEqual(response[0], POS_READ_DTC)
                self.assertEqual(
                    response[1], DTC_STATUS_AVAILABILITY_MASK,
                    "第 2 字节必须是 availability mask",
                )
                self.assertNotEqual(response[1], report_type)

    def test_severity_family_follows_the_same_profile(self) -> None:
        responses = {
            0x07: handle_uds_request(bytes([0x19, 0x07, 0xE0, 0xFF]), self.state),
            0x08: handle_uds_request(bytes([0x19, 0x08, 0xE0, 0xFF]), self.state),
            0x09: handle_uds_request(
                bytes([0x19, 0x09, 0x10, 0x00, 0x01]), self.state
            ),
        }

        for report_type, response in responses.items():
            with self.subTest(report_type=hex(report_type)):
                self.assertEqual(response[0], POS_READ_DTC)
                self.assertEqual(response[1], DTC_STATUS_AVAILABILITY_MASK)
                self.assertNotEqual(response[1], report_type)


# ======================================================================
# OQ6-A：DTC runtime state（mutate → 0x19 → 0x14 → cleared 闭环）
# ======================================================================


class TestDtcRuntimeRegistration(DtcTestCase):
    """已知 / 未知 DTC 的判定边界。"""

    def test_every_database_key_is_registered(self) -> None:
        for dtc in DTC_DATABASE:
            with self.subTest(dtc=hex(dtc)):
                self.assertTrue(dtc_runtime.is_registered_dtc(dtc))

    def test_dtc_outside_the_database_is_not_registered(self) -> None:
        for dtc in (0x000000, 0x100009, 0x300003, 0xFFFFFF):
            with self.subTest(dtc=hex(dtc)):
                self.assertFalse(dtc_runtime.is_registered_dtc(dtc))


class TestDtcRuntimeMutation(DtcTestCase):
    """set / get 的语义与校验（全部在写入前拒绝）。"""

    def test_initial_status_comes_from_the_database(self) -> None:
        for dtc, status in DTC_DATABASE.items():
            with self.subTest(dtc=hex(dtc)):
                self.assertEqual(dtc_runtime.get_dtc_status(self.state, dtc), status)

    def test_set_then_get_roundtrip(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        self.assertEqual(dtc_runtime.get_dtc_status(self.state, 0x100001), 0x09)

    def test_status_zero_means_inactive(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x00)

        self.assertEqual(dtc_runtime.get_dtc_status(self.state, 0x100001), 0x00)

    def test_status_boundaries_are_accepted(self) -> None:
        for status in (0x00, 0x01, 0x7F, 0x80, 0xFF):
            with self.subTest(status=hex(status)):
                dtc_runtime.set_dtc_status(self.state, 0x100001, status)

                self.assertEqual(
                    dtc_runtime.get_dtc_status(self.state, 0x100001), status
                )

    def test_repeat_set_is_idempotent(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100002, 0x04)
        once = dict(self.state.dtc_records)

        dtc_runtime.set_dtc_status(self.state, 0x100002, 0x04)

        self.assertEqual(self.state.dtc_records, once)

    def test_set_does_not_touch_other_dtc(self) -> None:
        before = dict(self.state.dtc_records)

        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x0F)

        for dtc, status in before.items():
            if dtc == 0x100001:
                continue

            with self.subTest(dtc=hex(dtc)):
                self.assertEqual(self.state.dtc_records[dtc], status)

    def test_set_does_not_touch_metadata(self) -> None:
        """severity / functionalUnit 是定义属性，不随 runtime status 改变。"""
        before = dict(DTC_METADATA)

        dtc_runtime.set_dtc_status(self.state, 0x100001, 0xFF)

        self.assertEqual(dict(DTC_METADATA), before)

    def test_invalid_status_is_rejected_without_write(self) -> None:
        snap = dict(self.state.dtc_records)

        for bad in (-1, 0x100, 0xFFFF):
            with self.subTest(status=bad):
                with self.assertRaises(dtc_runtime.DtcStateError):
                    dtc_runtime.set_dtc_status(self.state, 0x100001, bad)

        self.assertEqual(self.state.dtc_records, snap, "被拒的写入不得触碰 state")

    def test_unknown_dtc_is_rejected_and_not_created(self) -> None:
        """不静默创建没有 metadata 的新 DTC。"""
        unknown = 0x100009

        with self.assertRaises(dtc_runtime.UnknownDtcError):
            dtc_runtime.set_dtc_status(self.state, unknown, 0x01)

        self.assertNotIn(unknown, self.state.dtc_records, "不得凭空多出一个 DTC")

    def test_unknown_dtc_read_is_rejected(self) -> None:
        with self.assertRaises(dtc_runtime.UnknownDtcError):
            dtc_runtime.get_dtc_status(self.state, 0x100009)


class TestDtcRuntimeVisibleThrough0x19(DtcTestCase):
    """
    runtime mutation 必须能被**现有** 0x19 报告观察到
    —— 这是 "mutate → runtime state → 0x19" 这一段的闭环。
    """

    def test_01_count_reflects_mutation(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        response = handle_uds_request(bytes([0x19, 0x01, 0x09]), self.state)

        self.assertEqual(response[-2:], bytes([0x00, 0x01]), "只有 0x100001 命中")

    def test_02_record_reflects_mutation(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        response = handle_uds_request(bytes([0x19, 0x02, 0x09]), self.state)

        self.assertEqual(response, bytes.fromhex("59 FF 10 00 01 09"))

    def test_07_count_reflects_mutation(self) -> None:
        """
        severity(0x80) + status(0x08) 双 mask：mutation 前 0，后 1。

        statusMask 必须选默认 status 不匹配的位：0x100001 出厂 status 为
        0x01，``0x01 & 0x08 == 0`` → 变异前不命中；写入 0x08 后命中。
        （severityMask 0x80 只有 0x100001 具备，因此计数即它一个。）
        """
        before = handle_uds_request(bytes([0x19, 0x07, 0x80, 0x08]), self.state)

        self.assertEqual(before[-2:], bytes([0x00, 0x00]))

        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x08)

        after = handle_uds_request(bytes([0x19, 0x07, 0x80, 0x08]), self.state)

        self.assertEqual(after[-2:], bytes([0x00, 0x01]))

    def test_08_record_reflects_mutation(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        response = handle_uds_request(bytes([0x19, 0x08, 0x80, 0x09]), self.state)

        self.assertEqual(response, bytes.fromhex("59 FF 80 01 10 00 01 09"))

    def test_09_severity_stays_correct_after_mutation(self) -> None:
        """mutation 只改 status；severity / functionalUnit 必须保持定义值。"""
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        response = handle_uds_request(
            bytes([0x19, 0x09, 0x10, 0x00, 0x01]), self.state
        )

        self.assertEqual(response, bytes.fromhex("59 FF 80 01 10 00 01 09"))

    def test_0a_still_reports_every_supported_dtc(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        response = handle_uds_request(bytes([0x19, 0x0A]), self.state)

        self.assertEqual(len(response), 2 + 4 * len(DTC_DATABASE))


class TestDtcRuntimeClearInteraction(DtcTestCase):
    """
    "0x19 → 0x14 → cleared" 这一段的闭环：0x14 清除后所有报告都反映清除结果。
    """

    def test_single_clear_reverts_every_report(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x09)

        self.assertEqual(
            handle_uds_request(bytes([0x14, 0x10, 0x00, 0x01]), self.state),
            bytes([0x54]),
        )

        # 01：mask 0x09 不再命中
        self.assertEqual(
            handle_uds_request(bytes([0x19, 0x01, 0x09]), self.state)[-2:],
            bytes([0x00, 0x00]),
        )
        # 02：mask 0x09 无记录
        self.assertEqual(
            handle_uds_request(bytes([0x19, 0x02, 0x09]), self.state),
            bytes.fromhex("59 FF"),
        )
        # 07 / 08：severity 仍匹配但 status 已归零 → 不再命中
        self.assertEqual(
            handle_uds_request(bytes([0x19, 0x07, 0x80, 0x09]), self.state)[-2:],
            bytes([0x00, 0x00]),
        )
        self.assertEqual(
            handle_uds_request(bytes([0x19, 0x08, 0x80, 0x09]), self.state),
            bytes.fromhex("59 FF"),
        )
        # 09：DTC 仍在，status 归零，severity 不变
        self.assertEqual(
            handle_uds_request(bytes([0x19, 0x09, 0x10, 0x00, 0x01]), self.state),
            bytes.fromhex("59 FF 80 01 10 00 01 00"),
        )

    def test_all_clear_zeroes_a_mutated_status(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100002, 0x0F)

        self.assertEqual(
            handle_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), self.state),
            bytes([0x54]),
        )
        self.assertEqual(dtc_runtime.get_dtc_status(self.state, 0x100002), 0x00)
        self.assertEqual(len(self.state.dtc_records), len(DTC_DATABASE), "key 保留")

    def test_mutation_after_clear_is_normal(self) -> None:
        handle_uds_request(bytes([0x14, 0xFF, 0xFF, 0xFF]), self.state)

        dtc_runtime.set_dtc_status(self.state, 0x100003, 0x04)

        self.assertEqual(
            handle_uds_request(bytes([0x19, 0x01, 0x04]), self.state)[-2:],
            bytes([0x00, 0x01]),
        )


class TestDtcRuntimeResetInteraction(DtcTestCase):
    """0x11 01/02/03 都不清 DTC —— 与既有语义一致（DTC 是故障记忆）。"""

    def test_mutation_survives_every_reset_type(self) -> None:
        dtc_runtime.set_dtc_status(self.state, 0x100001, 0x02)

        for reset_type in (0x01, 0x02, 0x03):
            with self.subTest(reset_type=hex(reset_type)):
                handle_uds_request(bytes([SID_ECU_RESET, reset_type]), self.state)

                self.assertEqual(
                    dtc_runtime.get_dtc_status(self.state, 0x100001), 0x02
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
