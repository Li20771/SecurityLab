#!/usr/bin/env python3
"""
UDS Basic —— 0x31 RoutineControl 离线单元测试

直接调用协议层 ``uds_server``，不依赖 vcan0 / SocketCAN / ISO-TP。

覆盖（本 feature scope）:

1. routine 静态数据库约束（RID 2 字节、非空）
2. 0x31/01 StartRoutine（0x2001 / 0x2002、未知 RID、未知 subFunction、长度错）
3. 0x31/02 StopRoutine（active / inactive）
4. 0x31/03 RequestRoutineResults（0x2001 count、0x2002 魔数、
   未 Start 可查、纯读不改状态）
5. NRC 逐项 exact assert（12 / 13 / 22 / 31）
6. 状态机：Start / 重复 Start / Stop / 重复 Stop / Start→Stop→Start /
   多轮 count 累计
7. hardReset：routine 状态清空 + dtc_records 保留（成对验证）
8. session 切换：routine 状态保持不变
9. 实例隔离：state_a 的操作不影响 state_b；mutable default 不共享

明确**不测**：0x33 / 0x7E / 0x7F（权限/会话门控，属未来 framework）、
routineControlOptionRecord / routineStatusRecord（本 feature 不实现）。

运行：

    /home/lilike/.venvs/network/bin/python tests/test_routine_unit.py

或：

    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

from routine_database import (  # noqa: E402
    MAGIC_ECHO_RESULT,
    ROUTINE_MAGIC_ECHO,
    ROUTINE_NAMES,
    ROUTINE_START_COUNTER,
)
from uds_server import EcuState, handle_uds_request  # noqa: E402

SID_ROUTINE_CONTROL = 0x31
POS_ROUTINE_CONTROL = 0x71

NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE = 0x31


def _routine(sub_function: int, routine_id: int) -> bytes:
    """构造标准 4 字节请求：31 <sub> <RID:2>。"""
    return bytes([SID_ROUTINE_CONTROL, sub_function,
                  (routine_id >> 8) & 0xFF, routine_id & 0xFF])


class RoutineTestCase(unittest.TestCase):
    """每个测试使用独立 EcuState 实例，互不污染。"""

    def setUp(self) -> None:
        self.state = EcuState()

    def assertNegative(self, response: bytes, nrc: int) -> None:
        self.assertEqual(
            response,
            bytes([0x7F, SID_ROUTINE_CONTROL, nrc]),
            f"期望 7F 31 {nrc:02X}，实际 {response.hex(' ')}",
        )

    def snapshot(self) -> tuple:
        """返回 (active_routines, start_counts) 快照用于"状态不变"断言。"""
        return (set(self.state.active_routines),
                dict(self.state.routine_start_counts))


# ----------------------------------------------------------------------
# 1. 静态数据库约束
# ----------------------------------------------------------------------


class TestRoutineDatabase(RoutineTestCase):

    def test_database_is_not_empty(self) -> None:
        self.assertTrue(ROUTINE_NAMES, "ROUTINE_NAMES 不能为空")

    def test_rids_are_two_bytes(self) -> None:
        for rid in ROUTINE_NAMES:
            with self.subTest(rid=rid):
                self.assertGreaterEqual(rid, 0x0000)
                self.assertLessEqual(rid, 0xFFFF)

    def test_magic_echo_result_is_four_bytes(self) -> None:
        self.assertEqual(len(MAGIC_ECHO_RESULT), 4)

    def test_new_state_fields_are_empty(self) -> None:
        """EcuState() 新实例的 routine 字段为空，且不共享可变默认值。"""
        self.assertEqual(self.state.active_routines, set())
        self.assertEqual(self.state.routine_start_counts, {})


# ----------------------------------------------------------------------
# 2. 0x31/01 StartRoutine
# ----------------------------------------------------------------------


class TestStartRoutine(RoutineTestCase):

    def test_start_2001(self) -> None:
        response = handle_uds_request(
            _routine(0x01, ROUTINE_START_COUNTER), self.state)

        self.assertEqual(response, bytes([0x71, 0x01, 0x20, 0x01]))
        self.assertIn(ROUTINE_START_COUNTER, self.state.active_routines)
        self.assertEqual(
            self.state.routine_start_counts[ROUTINE_START_COUNTER], 1)

    def test_start_2002(self) -> None:
        response = handle_uds_request(
            _routine(0x01, ROUTINE_MAGIC_ECHO), self.state)

        self.assertEqual(response, bytes([0x71, 0x01, 0x20, 0x02]))
        self.assertIn(ROUTINE_MAGIC_ECHO, self.state.active_routines)
        # magic-echo 没有计数语义
        self.assertNotIn(ROUTINE_MAGIC_ECHO, self.state.routine_start_counts)

    def test_double_start_rejected_without_state_change(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)
        before = self.snapshot()

        response = handle_uds_request(
            _routine(0x01, ROUTINE_START_COUNTER), self.state)

        self.assertNegative(response, NRC_CONDITIONS_NOT_CORRECT)
        self.assertEqual(self.snapshot(), before, "重复 Start 不得改变状态")

    def test_unknown_rid(self) -> None:
        self.assertNegative(
            handle_uds_request(_routine(0x01, 0xAAAA), self.state),
            NRC_REQUEST_OUT_OF_RANGE,
        )

    def test_unknown_sub_function(self) -> None:
        for sub in (0x00, 0x04, 0x05, 0xFF):
            with self.subTest(sub=sub):
                self.assertNegative(
                    handle_uds_request(
                        _routine(sub, ROUTINE_START_COUNTER), self.state),
                    NRC_SUBFUNCTION_NOT_SUPPORTED,
                )

    def test_bad_lengths(self) -> None:
        cases = (
            bytes([0x31]),
            bytes([0x31, 0x01]),
            bytes([0x31, 0x01, 0x20]),
            bytes([0x31, 0x01, 0x20, 0x01, 0x00]),
        )

        for request in cases:
            with self.subTest(length=len(request)):
                self.assertNegative(
                    handle_uds_request(request, self.state),
                    NRC_INCORRECT_MESSAGE_LENGTH,
                )

    def test_nrc_paths_do_not_mutate_state(self) -> None:
        before = self.snapshot()

        handle_uds_request(_routine(0x04, ROUTINE_START_COUNTER), self.state)
        handle_uds_request(_routine(0x01, 0xAAAA), self.state)
        handle_uds_request(bytes([0x31, 0x01]), self.state)

        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 3. 0x31/02 StopRoutine
# ----------------------------------------------------------------------


class TestStopRoutine(RoutineTestCase):

    def test_stop_active(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)

        response = handle_uds_request(
            _routine(0x02, ROUTINE_START_COUNTER), self.state)

        self.assertEqual(response, bytes([0x71, 0x02, 0x20, 0x01]))
        self.assertNotIn(ROUTINE_START_COUNTER, self.state.active_routines)

    def test_stop_inactive_rejected_without_state_change(self) -> None:
        before = self.snapshot()

        response = handle_uds_request(
            _routine(0x02, ROUTINE_START_COUNTER), self.state)

        self.assertNegative(response, NRC_CONDITIONS_NOT_CORRECT)
        self.assertEqual(self.snapshot(), before)


# ----------------------------------------------------------------------
# 4. 0x31/03 RequestRoutineResults
# ----------------------------------------------------------------------


class TestRequestRoutineResults(RoutineTestCase):

    def test_2001_results_before_start_is_zero(self) -> None:
        """Results 是纯查询：从未 Start 也允许查询，初始 count = 0。"""
        response = handle_uds_request(
            _routine(0x03, ROUTINE_START_COUNTER), self.state)

        self.assertEqual(
            response, bytes([0x71, 0x03, 0x20, 0x01, 0x00, 0x00, 0x00, 0x00]))

    def test_2001_results_after_start(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)

        response = handle_uds_request(
            _routine(0x03, ROUTINE_START_COUNTER), self.state)

        self.assertEqual(
            response, bytes([0x71, 0x03, 0x20, 0x01, 0x00, 0x00, 0x00, 0x01]))

    def test_2002_results_fixed_magic(self) -> None:
        response = handle_uds_request(
            _routine(0x03, ROUTINE_MAGIC_ECHO), self.state)

        self.assertEqual(
            response, bytes([0x71, 0x03, 0x20, 0x02]) + MAGIC_ECHO_RESULT)

    def test_2002_results_independent_of_start_count(self) -> None:
        for _ in range(3):
            handle_uds_request(_routine(0x01, ROUTINE_MAGIC_ECHO), self.state)
            handle_uds_request(_routine(0x02, ROUTINE_MAGIC_ECHO), self.state)

        response = handle_uds_request(
            _routine(0x03, ROUTINE_MAGIC_ECHO), self.state)

        self.assertEqual(
            response, bytes([0x71, 0x03, 0x20, 0x02]) + MAGIC_ECHO_RESULT)

    def test_results_does_not_mutate_state(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)
        before = self.snapshot()

        handle_uds_request(_routine(0x03, ROUTINE_START_COUNTER), self.state)
        handle_uds_request(_routine(0x03, ROUTINE_MAGIC_ECHO), self.state)

        self.assertEqual(self.snapshot(), before, "Results 不得改变任何状态")


# ----------------------------------------------------------------------
# 5. 状态机
# ----------------------------------------------------------------------


class TestRoutineStateMachine(RoutineTestCase):

    def test_start_stop_start_cycle(self) -> None:
        self.assertEqual(
            handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state),
            bytes([0x71, 0x01, 0x20, 0x01]))
        self.assertEqual(
            handle_uds_request(_routine(0x02, ROUTINE_START_COUNTER), self.state),
            bytes([0x71, 0x02, 0x20, 0x01]))
        self.assertEqual(
            handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state),
            bytes([0x71, 0x01, 0x20, 0x01]))
        self.assertEqual(
            self.state.routine_start_counts[ROUTINE_START_COUNTER], 2)

    def test_multi_cycle_count_accumulates(self) -> None:
        for expected in range(1, 4):
            handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)
            handle_uds_request(_routine(0x02, ROUTINE_START_COUNTER), self.state)

            response = handle_uds_request(
                _routine(0x03, ROUTINE_START_COUNTER), self.state)

            self.assertEqual(
                response[-4:], expected.to_bytes(4, "big"))

    def test_two_routines_have_independent_state(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)
        handle_uds_request(_routine(0x01, ROUTINE_MAGIC_ECHO), self.state)

        self.assertIn(ROUTINE_START_COUNTER, self.state.active_routines)
        self.assertIn(ROUTINE_MAGIC_ECHO, self.state.active_routines)

        # 停掉其中一个，另一个保持 active
        self.assertEqual(
            handle_uds_request(_routine(0x02, ROUTINE_START_COUNTER), self.state),
            bytes([0x71, 0x02, 0x20, 0x01]))
        self.assertIn(ROUTINE_MAGIC_ECHO, self.state.active_routines)
        self.assertNotIn(ROUTINE_START_COUNTER, self.state.active_routines)


# ----------------------------------------------------------------------
# 6. hardReset：routine 清空 / DTC 保留（成对验证）
# ----------------------------------------------------------------------


class TestRoutineResetSemantics(RoutineTestCase):

    def test_hard_reset_clears_routine_and_preserves_dtc(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)
        handle_uds_request(_routine(0x01, ROUTINE_MAGIC_ECHO), self.state)
        self.state.dtc_records[0x100009] = 0x09  # 自定义运行时 DTC

        dtc_before = dict(self.state.dtc_records)

        reset_response = handle_uds_request(bytes([0x11, 0x01]), self.state)
        self.assertEqual(reset_response, bytes([0x51, 0x01]))

        # routine 状态：清空
        self.assertEqual(self.state.active_routines, set())
        self.assertEqual(self.state.routine_start_counts, {})

        # DTC 故障记忆：保留
        self.assertEqual(self.state.dtc_records, dtc_before)
        self.assertIn(0x100009, self.state.dtc_records)

        # 复位后：Stop → 0x22（routine 已 inactive）；Results → count 0
        self.assertNegative(
            handle_uds_request(_routine(0x02, ROUTINE_START_COUNTER), self.state),
            NRC_CONDITIONS_NOT_CORRECT,
        )
        self.assertEqual(
            handle_uds_request(_routine(0x03, ROUTINE_START_COUNTER), self.state)[-4:],
            bytes([0x00, 0x00, 0x00, 0x00]),
        )


# ----------------------------------------------------------------------
# 7. session 切换：routine 状态保持不变
# ----------------------------------------------------------------------


class TestRoutineSessionBehavior(RoutineTestCase):

    def test_session_switch_preserves_routine_state(self) -> None:
        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)

        handle_uds_request(bytes([0x10, 0x03]), self.state)
        self.assertEqual(self.state.session, 0x03)

        # routine 仍 active，Results 仍可读
        self.assertIn(ROUTINE_START_COUNTER, self.state.active_routines)
        self.assertEqual(
            handle_uds_request(_routine(0x03, ROUTINE_START_COUNTER), self.state)[-4:],
            bytes([0x00, 0x00, 0x00, 0x01]),
        )

        # 会话切回 default，routine 状态依旧
        handle_uds_request(bytes([0x10, 0x01]), self.state)
        self.assertIn(ROUTINE_START_COUNTER, self.state.active_routines)


# ----------------------------------------------------------------------
# 8. 实例隔离
# ----------------------------------------------------------------------


class TestRoutineInstanceIsolation(RoutineTestCase):

    def test_state_a_does_not_affect_state_b(self) -> None:
        other = EcuState()

        handle_uds_request(_routine(0x01, ROUTINE_START_COUNTER), self.state)

        self.assertNotIn(ROUTINE_START_COUNTER, other.active_routines)
        self.assertEqual(other.routine_start_counts, {})

        # state_b 的 Results 保持 count=0
        self.assertEqual(
            handle_uds_request(_routine(0x03, ROUTINE_START_COUNTER), other)[-4:],
            bytes([0x00, 0x00, 0x00, 0x00]),
        )

    def test_mutable_defaults_are_not_shared(self) -> None:
        first = EcuState()
        second = EcuState()

        first.active_routines.add(ROUTINE_START_COUNTER)
        first.routine_start_counts[ROUTINE_START_COUNTER] = 5

        self.assertEqual(second.active_routines, set())
        self.assertEqual(second.routine_start_counts, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
