#!/usr/bin/env python3
"""
UDS Basic —— C1 授权契约冻结 pin（C1 Implementation Preflight）

**用途**：在 C1 Permission / Session Framework 实施**之前**，把若干**当前真实存在、
但既有 907 用例未覆盖**的授权 / NRC 行为固化为可执行断言。

**为什么需要本文件**：设计冻结文档
``docs/OQ_C1_PERMISSION_SESSION_DESIGN_REVIEW_2026-09-24.md`` §15.4 记录了七个覆盖缺口。
其中 N1/N2/N3 属于"**迁移时会被静默改变、而既有 907 用例抓不到**"的行为：

    N1  0x2E：未知 DID 判定**先于**默认会话门控（``2E FF FF`` → ``7F 2E 31``）
    N2  0x22：NRC **依赖 DID 在请求中的顺序**（受保护在前 → ``33``；未知在前 → ``31``）
    N3  0x14 / 0x19 / 0x85 在三种会话下行为**完全相同**
        —— 既有测试**从未**在非默认会话下运行过这三个服务
    N4  0x2E：安全门控**先于**"只读 DID"判定（``0x33`` 先于 ``0x22``）
    N5  0x27 的 seed 过期 / 锁定到期 —— **已由 ``test_ecu_unit.py`` 覆盖**（见 §N5 注）
    N6  ``handle_uds_request(..., services=...)`` 覆盖缝的当前契约
    N7  静态 ``SERVICES`` 注册表的 key set（含"``0x27`` 不在表内"这一注入边界）

**本文件只新增断言，不修改任何生产代码。** 它使 C1 迁移可以"先冻结 → 再迁移 →
逐字节对拍"，而不是依赖"迁移后 907 全绿"这种**无法发现 NRC 优先级变化**的判据。

**所有期望字节均来自对当前源码的实测**（非推断）；若将来某个断言失败，
说明**行为已改变**，应先判断该改变是否为 C1 的有意设计，再决定是否更新本文件。

运行：

    python3 tests/test_c1_contract_pins.py
或：
    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import uds_types  # noqa: E402
from did_database import DID_DATABASE  # noqa: E402
from uds_server import SERVICES, EcuState, handle_uds_request  # noqa: E402

# ---- SID / 正响应 SID ----
SID_READ = 0x22
POS_READ = 0x62
SID_WRITE = 0x2E
POS_WRITE = 0x6E
SID_SESSION = 0x10
SID_CLEAR_DTC = 0x14
POS_CLEAR_DTC = 0x54
SID_READ_DTC = 0x19
SID_TESTER_PRESENT = 0x3E
SID_CONTROL_DTC_SETTING = 0x85

# ---- DID ----
DID_WRITABLE = 0xF102          # 可写（WRITABLE_DIDS）
DID_READONLY = 0xF190           # 已知但只读（VIN）
DID_PROTECTED = 0xF187          # 受保护：读取需 SECURITY_LEVEL1（PROTECTED_DIDS）
DID_UNKNOWN = 0xFFFF

# ---- NRC ----
NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH = 0x13
NRC_CONDITIONS_NOT_CORRECT = 0x22
NRC_REQUEST_OUT_OF_RANGE = 0x31
NRC_SECURITY_ACCESS_DENIED = 0x33
NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F

#: 静态注册表应有的**精确 key set**（N7）。当前 14 项，**不含 0x27**。
EXPECTED_STATIC_SERVICES = frozenset(
    {0x10, 0x11, 0x14, 0x19, 0x22, 0x2E, 0x31, 0x34, 0x35, 0x36, 0x37, 0x38, 0x3E, 0x85}
)

#: 0x19/0A（supportedDTC）的确定性正响应（3 个合成 DTC）。
DTC_SUPPORTED_RESPONSE = bytes.fromhex("59 ff 10 00 01 01 10 00 02 02 10 00 03 04")

#: 0x22/F190 的确定性正响应（VIN 出厂值）。
VIN_RESPONSE = bytes([POS_READ, 0xF1, 0x90]) + DID_DATABASE[0xF190]

SESSION_SWITCH_RESPONSES = {
    uds_types.SESSION_DEFAULT: bytes.fromhex("50 01 00 32 00 64"),
    uds_types.SESSION_EXTENDED: bytes.fromhex("50 03 00 64 03 e8"),
    uds_types.SESSION_PROGRAMMING: bytes.fromhex("50 02 00 c8 13 88"),
}


def neg(sid: int, nrc: int) -> bytes:
    """构造否定响应 ``7F <sid> <nrc>``。"""
    return bytes([0x7F, sid, nrc])


def unlock_extended(state: EcuState) -> None:
    """把状态置为 extendedDiagnosticSession + SecurityAccess LEVEL1。"""
    state.session = uds_types.SESSION_EXTENDED
    state.security_level = uds_types.SECURITY_LEVEL1


class C1PinTestCase(unittest.TestCase):
    """公共装置：每个用例一个全新 ``EcuState``（协议层，不经 0x27 注入）。"""

    def setUp(self) -> None:
        self.state = EcuState()

    def send(self, request: bytes) -> bytes:
        return handle_uds_request(request, self.state)


# ----------------------------------------------------------------------
# N1 —— 0x2E：未知 DID 判定先于默认会话门控
# ----------------------------------------------------------------------


class TestN1WriteDidGateOrder(C1PinTestCase):
    """
    N1：``0x2E`` 的门控顺序是
    ``格式(13) → 未知 DID(31) → 会话(7F) → 安全(33) → 只读 DID(22) → 长度(13)``。

    既有测试**只**在 extended(+unlocked) 下断言过 ``31``，或在默认会话下对**已知** DID
    断言 ``7F`` —— **没有任何用例**同时覆盖"默认会话 + 未知 DID"。
    若 C1 把会话门控前置到 dispatcher，``2E FF FF`` 会由 ``31`` 静默变成 ``7F``。
    """

    def test_unknown_did_out_of_range_precedes_default_session_gate(self):
        """默认会话 + 未知 DID → ``7F 2E 31``（DID 判定先发生）。"""
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xFF, 0xFF]) + b"X"),
            neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE),
        )

    def test_known_did_in_default_session_hits_session_gate(self):
        """默认会话 + 已知可写 DID → ``7F 2E 7F``（只有越过 DID 判定才会撞会话门控）。"""
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x02]) + b"X"),
            neg(SID_WRITE, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION),
        )

    def test_both_outcomes_in_one_state(self):
        """
        同一状态下的对照：两者都发生在**默认会话**，仅 DID 是否已知不同 → NRC 不同。

        这条断言把"顺序"从"两个独立用例的巧合"变成**同一状态下的一次对照**。
        """
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xFF, 0xFF]) + b"X"),
            neg(SID_WRITE, NRC_REQUEST_OUT_OF_RANGE),
        )
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x02]) + b"X"),
            neg(SID_WRITE, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION),
        )
        self.assertEqual(self.state.session, uds_types.SESSION_DEFAULT)


# ----------------------------------------------------------------------
# N2 —— 0x22：NRC 依赖 DID 在请求中的顺序
# ----------------------------------------------------------------------


class TestN2ReadDidNrcDependsOnDidOrder(C1PinTestCase):
    """
    N2：``0x22`` 对多个 DID 是 **all-or-nothing**，且**逐 DID 短路**：
    第一个失败的 DID 决定 NRC。因此 NRC **依赖 DID 在请求中的顺序**。

    **任何"先解析全部 DID 再统一判定"的全局前置层都会改变这里的行为。**
    既有测试没有把"未知 DID"与"受保护 DID"放进同一请求，故该行为当前无保护。
    """

    def test_protected_did_first_yields_security_denied(self):
        """受保护 DID 在前（未解锁）→ ``7F 22 33``。"""
        self.assertEqual(
            self.send(bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF])),
            neg(SID_READ, NRC_SECURITY_ACCESS_DENIED),
        )

    def test_unknown_did_first_yields_out_of_range(self):
        """未知 DID 在前 → ``7F 22 31``（**同一组 DID，仅顺序不同**）。"""
        self.assertEqual(
            self.send(bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87])),
            neg(SID_READ, NRC_REQUEST_OUT_OF_RANGE),
        )

    def test_did_order_is_the_only_difference(self):
        """把上两条放在一起：同一状态、同一 DID 集合，**只有顺序不同** → NRC 不同。"""
        protected_first = self.send(bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF]))
        self.state = EcuState()
        unknown_first = self.send(bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87]))

        self.assertEqual(protected_first, neg(SID_READ, NRC_SECURITY_ACCESS_DENIED))
        self.assertEqual(unknown_first, neg(SID_READ, NRC_REQUEST_OUT_OF_RANGE))
        self.assertNotEqual(protected_first, unknown_first)

    def test_public_did_first_then_unknown_yields_out_of_range(self):
        """公开 DID 在前（未解锁，可读）→ 仍因后面的未知 DID 整体失败 ``31``。"""
        self.assertEqual(
            self.send(bytes([SID_READ, 0xF1, 0x90, 0xFF, 0xFF])),
            neg(SID_READ, NRC_REQUEST_OUT_OF_RANGE),
        )


# ----------------------------------------------------------------------
# N3 —— 非默认会话下的 0x14 / 0x19 / 0x85（**冻结"无会话门控"这一事实**）
# ----------------------------------------------------------------------


class TestN3ServicesWithoutSessionGating(C1PinTestCase):
    """
    N3：``0x14`` / ``0x19`` / ``0x85`` 在 **DEFAULT / EXTENDED / PROGRAMMING** 三种会话下
    行为**逐字节完全相同** —— 即它们**当前没有任何会话门控**。

    这一事实**既有测试从未验证过**：DTC 相关的四个测试文件（``test_dtc_unit.py`` /
    ``test_dtc_clear_unit.py`` / ``test_dtc_vcan.py`` / ``test_dtc_clear_vcan.py``）
    **没有任何会话切换**，因此这三个服务只在默认会话下被跑过。

    **本用例的目的不是给它们增加权限限制**，而是把"当前无门控"写成断言：
    将来若把授权做成 dispatcher 全局前置，这三个服务会被静默改变，而本文件会立即失败。
    """

    SESSIONS = (
        uds_types.SESSION_DEFAULT,
        uds_types.SESSION_EXTENDED,
        uds_types.SESSION_PROGRAMMING,
    )

    def _with_session(self, session: int) -> bytes:
        """切到指定会话（用真实 0x10 请求），返回切换响应。"""
        return self.send(bytes([SID_SESSION, session]))

    def test_clear_dtc_is_session_independent(self):
        """``0x14`` 全清：三种会话下均为 ``54``。"""
        for session in self.SESSIONS:
            with self.subTest(session=hex(session)):
                self.state = EcuState()
                self.assertEqual(
                    self._with_session(session), SESSION_SWITCH_RESPONSES[session]
                )
                self.assertEqual(
                    self.send(bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF])),
                    bytes([POS_CLEAR_DTC]),
                )

    def test_read_dtc_is_session_independent(self):
        """``0x19 0A``：三种会话下响应逐字节相同。"""
        for session in self.SESSIONS:
            with self.subTest(session=hex(session)):
                self.state = EcuState()
                self.assertEqual(
                    self._with_session(session), SESSION_SWITCH_RESPONSES[session]
                )
                self.assertEqual(
                    self.send(bytes([SID_READ_DTC, 0x0A])), DTC_SUPPORTED_RESPONSE
                )

    def test_control_dtc_setting_is_session_independent(self):
        """``0x85 01``：三种会话下均为 ``C5 01``。"""
        for session in self.SESSIONS:
            with self.subTest(session=hex(session)):
                self.state = EcuState()
                self.assertEqual(
                    self._with_session(session), SESSION_SWITCH_RESPONSES[session]
                )
                self.assertEqual(
                    self.send(bytes([SID_CONTROL_DTC_SETTING, 0x01])),
                    bytes([0xC5, 0x01]),
                )

    def test_the_three_services_are_not_session_gated_at_all(self):
        """
        把三个服务放一起、跨三种会话做**同值断言**：

        收集每个服务在三种会话下的响应，要求各自"三值相同"。
        这是对"无会话门控"最直接的表达（而不是三个独立的近似用例）。
        """
        for label, request in (
            ("0x14", bytes([SID_CLEAR_DTC, 0xFF, 0xFF, 0xFF])),
            ("0x19/0A", bytes([SID_READ_DTC, 0x0A])),
            ("0x85/01", bytes([SID_CONTROL_DTC_SETTING, 0x01])),
        ):
            with self.subTest(service=label):
                responses = []
                for session in self.SESSIONS:
                    self.state = EcuState()
                    self._with_session(session)
                    responses.append(self.send(request))

                self.assertEqual(
                    len(set(responses)),
                    1,
                    f"{label} 在三种会话下响应不一致："
                    f"{[r.hex(' ') for r in responses]} —— 说明该服务**被新增了会话门控**，"
                    f"这是行为变更（C1 不得静默引入）",
                )


# ----------------------------------------------------------------------
# N4 —— 0x2E：安全门控先于"只读 DID"判定
# ----------------------------------------------------------------------


class TestN4SecurityPrecedesWritability(C1PinTestCase):
    """
    N4：``0x2E`` 的第 4 级（安全 `33`）先于第 5 级（只读 DID `22`）。

    既有测试对只读 DID 的断言都在 ``unlock_extended()`` **之后**执行，
    因此"锁定状态下只读 DID 返回 `33` 而非 `22`"这一顺序**当前无保护**。
    """

    def test_readonly_did_while_locked_is_security_denied(self):
        """extended + locked + 只读 DID → ``7F 2E 33``（安全门控先发生）。"""
        self.state.session = uds_types.SESSION_EXTENDED
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x90]) + b"X"),
            neg(SID_WRITE, NRC_SECURITY_ACCESS_DENIED),
        )

    def test_readonly_did_while_unlocked_is_conditions_not_correct(self):
        """extended + unlocked + 只读 DID → ``7F 2E 22``（越过安全门控后才撞只读判定）。"""
        unlock_extended(self.state)
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x90]) + b"X"),
            neg(SID_WRITE, NRC_CONDITIONS_NOT_CORRECT),
        )


# ----------------------------------------------------------------------
# N5 —— 0x27 的 seed 过期 / 锁定到期（**已覆盖，此处不重复**）
# ----------------------------------------------------------------------


class TestN5SecurityAccessTimeBehaviourIsAlreadyCovered(unittest.TestCase):
    """
    N5：**刻意不加重复用例。**

    经核查，``0x27`` 的时间相关行为**已由既有 unit 覆盖**（通过
    ``mock.patch.object(ecu, "_now", ...)`` 注入时钟）：

        test_ecu_unit.py:1142  test_expired_seed_returns_sequence_error   （seed 过期 → 0x24）
        test_ecu_unit.py:1157  test_seed_valid_just_before_expiry         （过期边界）
        test_ecu_unit.py:1242  test_lockout_expires_allows_retry          （锁定到期可重试）
        test_ecu_unit.py:1268  test_failed_attempts_reset_after_lockout   （到期后计数归零）

    已记录的**唯一缺口**是 e2e 侧（``test_e2e_vcan.py`` 中无 seed 过期 / 锁定到期用例），
    但那属于 vcan 层，且本步骤**不扩大 SecurityAccess 设计**，故只在此记录事实。

    本类保留一条**结构性断言**：确认这些用例确实仍存在于既有文件（防止被误删），
    从而让"已覆盖"这一判断本身可被回归验证。
    """

    def test_time_related_security_access_tests_still_exist(self):
        """既有 unit 文件中仍存在 4 个时间相关 0x27 用例（本步骤不重复它们）。"""
        unit_path = os.path.join(_HERE, "test_ecu_unit.py")
        with open(unit_path, encoding="utf-8") as handle:
            source = handle.read()

        for name in (
            "test_expired_seed_returns_sequence_error",
            "test_seed_valid_just_before_expiry",
            "test_lockout_expires_allows_retry",
            "test_failed_attempts_reset_after_lockout",
        ):
            with self.subTest(test=name):
                self.assertIn(
                    f"def {name}",
                    source,
                    f"{name} 不存在于 test_ecu_unit.py —— 若被删除，"
                    f"0x27 时间行为将失去覆盖（C1 不得删除既有测试）",
                )


# ----------------------------------------------------------------------
# N6 —— handle_uds_request(..., services=...) 覆盖缝的当前契约
# ----------------------------------------------------------------------


class TestN6ServicesOverrideContract(C1PinTestCase):
    """
    N6：``handle_uds_request(request, state, *, services=...)`` 的**当前真实契约**。

    该缝是 C1 最可能触碰的迁移点（设计文档 OD-4），但此前**零测试覆盖**：
    全仓库唯一使用者是 ``ecu.py`` 的 0x27 注入。

    冻结的契约（实测）：

        services=None            → 使用静态 SERVICES
        services=自定义 mapping  → **完全取代**静态表（不做合并）
        自定义表缺某 SID         → 该 SID 返回 0x11（serviceNotSupported）
    """

    def test_explicit_static_table_is_equivalent_to_default(self):
        """显式传入 ``dict(SERVICES)`` 与默认路径**逐字节等价**。"""
        requests = (
            bytes([SID_TESTER_PRESENT, 0x00]),
            bytes([SID_READ, 0xF1, 0x90]),
            bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
            bytes([SID_READ_DTC, 0x0A]),
        )
        for request in requests:
            with self.subTest(request=request.hex(" ")):
                default_response = handle_uds_request(request, EcuState())
                override_response = handle_uds_request(
                    request, EcuState(), services=dict(SERVICES)
                )
                self.assertEqual(default_response, override_response)

    def test_override_replaces_the_table_and_missing_sid_is_unsupported(self):
        """覆盖表**取代**而非合并：只放 0x3E 时，0x22 变成未注册 SID → ``11``。"""
        override = {SID_TESTER_PRESENT: SERVICES[SID_TESTER_PRESENT]}

        self.assertEqual(
            handle_uds_request(bytes([SID_TESTER_PRESENT, 0x00]), EcuState(), services=override),
            bytes([0x7E, 0x00]),
        )
        self.assertEqual(
            handle_uds_request(bytes([SID_READ, 0xF1, 0x90]), EcuState(), services=override),
            neg(SID_READ, NRC_SERVICE_NOT_SUPPORTED),
        )

    def test_override_can_install_a_custom_handler(self):
        """覆盖表可安装自定义 handler（C1 迁移将依赖这一能力）。"""
        received = []

        def custom_handler(request: bytes, state: EcuState) -> bytes:
            received.append(request)
            return bytes([0x7E, 0x00])

        override = {SID_TESTER_PRESENT: custom_handler}
        response = handle_uds_request(
            bytes([SID_TESTER_PRESENT, 0x00]), EcuState(), services=override
        )

        self.assertEqual(response, bytes([0x7E, 0x00]))
        self.assertEqual(received, [bytes([SID_TESTER_PRESENT, 0x00])])


# ----------------------------------------------------------------------
# N7 —— 静态 SERVICES 注册表的 key set（含 0x27 注入边界）
# ----------------------------------------------------------------------


class TestN7StaticRegistryKeySet(unittest.TestCase):
    """
    N7：静态 ``SERVICES`` 的 key set 被精确冻结。

    目的：C1 迁移时**不得无意改变**注册表内容；尤其要保护
    "``0x27`` **不在**静态表内、由入口层注入"这一既有边界
    （设计文档 §12；由 ``test_gateway_runtime_vcan.py`` 在 vcan 层固化）。
    """

    def test_key_set_is_exactly_the_frozen_fourteen(self):
        """精确断言 14 个 SID（多一个或少一个都必须是有意的评审事件）。"""
        self.assertEqual(set(SERVICES), set(EXPECTED_STATIC_SERVICES))
        self.assertEqual(len(SERVICES), 14)

    def test_security_access_is_not_in_the_static_registry(self):
        """``0x27`` 不在静态表内 —— 它是入口层注入的能力，不是静态注册表能力。"""
        self.assertNotIn(0x27, SERVICES)

    def test_static_registry_has_no_authorization_metadata(self):
        """
        当前静态表的值是**纯 callable**，没有任何授权 metadata。

        这是 C1 要改变的对象 —— 在此记录**迁移前**的形态，使"是否改变了表的形状"
        成为一个可断言的、显式的事实（而非隐式）。
        """
        for sid, entry in SERVICES.items():
            with self.subTest(sid=hex(sid)):
                self.assertTrue(
                    callable(entry),
                    f"SERVICES[{hex(sid)}] 不是 callable —— 注册表形状已改变（C1 需显式评审）",
                )


# ----------------------------------------------------------------------
# F-a ~ F-g —— 设计文档 §15.5 要求冻结的高价值既有 pin
# ----------------------------------------------------------------------


class TestFaSessionSwitchRelocks(C1PinTestCase):
    """F-a：切会话把 ``security_level`` 归 LOCKED（三种会话类型全覆盖）。"""

    def test_every_session_switch_relocks(self):
        for session in (
            uds_types.SESSION_DEFAULT,
            uds_types.SESSION_EXTENDED,
            uds_types.SESSION_PROGRAMMING,
        ):
            with self.subTest(session=hex(session)):
                self.state = EcuState()
                unlock_extended(self.state)
                self.assertEqual(self.state.security_level, uds_types.SECURITY_LEVEL1)

                self.send(bytes([SID_SESSION, session]))

                self.assertEqual(self.state.session, session)
                self.assertEqual(self.state.security_level, uds_types.SECURITY_LOCKED)


class TestFbFcDidLevelPermission(C1PinTestCase):
    """F-b / F-c：**DID 级**（而非服务级）权限 —— 同一状态下公开 DID 可读、受保护 DID 被拒。"""

    def test_same_state_public_readable_and_protected_denied(self):
        for session in (
            uds_types.SESSION_DEFAULT,
            uds_types.SESSION_EXTENDED,
            uds_types.SESSION_PROGRAMMING,
        ):
            with self.subTest(session=hex(session)):
                self.state = EcuState()
                if session != uds_types.SESSION_DEFAULT:
                    self.send(bytes([SID_SESSION, session]))

                # 同一状态下：公开 DID 正响应，受保护 DID 被拒
                self.assertEqual(self.send(bytes([SID_READ, 0xF1, 0x90])), VIN_RESPONSE)
                self.assertEqual(
                    self.send(bytes([SID_READ, 0xF1, 0x87])),
                    neg(SID_READ, NRC_SECURITY_ACCESS_DENIED),
                )

    def test_protected_did_readable_when_unlocked(self):
        unlock_extended(self.state)
        response = self.send(bytes([SID_READ, 0xF1, 0x87]))
        self.assertEqual(response[0], POS_READ)
        self.assertEqual(response[3:], DID_DATABASE[0xF187])


class TestFdReadDidAllOrNothing(C1PinTestCase):
    """F-d：``0x22`` 多 DID all-or-nothing，且**不泄漏部分数据**。"""

    def test_multi_did_failure_returns_no_partial_data(self):
        self.state = EcuState()

        # 公开 DID 在前、受保护 DID 在后 → 整体失败
        response = self.send(bytes([SID_READ, 0xF1, 0x90, 0xF1, 0x87]))
        self.assertEqual(response, neg(SID_READ, NRC_SECURITY_ACCESS_DENIED))
        self.assertNotIn(
            DID_DATABASE[0xF190],
            response,
            "整体失败时不得泄漏前面 DID 的数据",
        )

    def test_multi_did_success_returns_all_payloads_in_order(self):
        unlock_extended(self.state)
        response = self.send(bytes([SID_READ, 0xF1, 0x90, 0xF1, 0x87]))

        self.assertEqual(response[0], POS_READ)
        self.assertIn(DID_DATABASE[0xF190], response)
        self.assertIn(DID_DATABASE[0xF187], response)
        # 顺序：请求顺序
        self.assertLess(
            response.index(DID_DATABASE[0xF190]), response.index(DID_DATABASE[0xF187])
        )


class TestFeWriteDidGateChain(C1PinTestCase):
    """F-e：``0x2E`` 的完整门控链 ``7F`` → ``33`` → ``22`` → 成功。"""

    def test_full_gate_chain_in_order(self):
        self.state = EcuState()
        # ① 默认会话 → 7F
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x02]) + b"X"),
            neg(SID_WRITE, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION),
        )

        # ② extended + locked → 33
        self.state.session = uds_types.SESSION_EXTENDED
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x02]) + b"X"),
            neg(SID_WRITE, NRC_SECURITY_ACCESS_DENIED),
        )

        # ③ extended + unlocked + 只读 DID → 22
        self.state.security_level = uds_types.SECURITY_LEVEL1
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x90]) + b"X"),
            neg(SID_WRITE, NRC_CONDITIONS_NOT_CORRECT),
        )

        # ④ extended + unlocked + 可写 DID → 6E（并读回）
        self.assertEqual(
            self.send(bytes([SID_WRITE, 0xF1, 0x02]) + b"PINNED"),
            bytes([POS_WRITE, 0xF1, 0x02]),
        )
        self.assertEqual(
            self.send(bytes([SID_READ, 0xF1, 0x02])),
            bytes([POS_READ, 0xF1, 0x02]) + b"PINNED",
        )


class TestFfSecurityAccessInjectionBoundary(C1PinTestCase):
    """
    F-f：``0x27`` 的**注入边界**（设计文档 §12 要求冻结的边界）。

    既有 unit 已覆盖 ``0x27`` 的状态机（35/35/36、锁定、`0x12` 优先于 `0x37`），
    故此处**不重复**；这里冻结的是**另一件事** —— 协议层静态表**不认识** ``0x27``：

        uds_server.handle_uds_request(静态表) → 27 01 → 7F 27 11

    由此保证：任何"把 0x27 挪进静态表"的改动（C1 或 C3）都会**立即**被本用例发现。
    """

    def test_static_protocol_layer_does_not_implement_security_access(self):
        self.assertEqual(
            self.send(bytes([0x27, 0x01])),
            neg(0x27, NRC_SERVICE_NOT_SUPPORTED),
        )


class TestFgSessionSwitchDoesNotResetNonGatedState(C1PinTestCase):
    """F-g：切会话**不得**重置非门控服务的状态。"""

    def test_control_dtc_setting_off_survives_session_switch(self):
        """``0x85 02``（冻结更新）在切会话后保持生效（会话切换不是复位）。"""
        self.assertEqual(
            self.send(bytes([SID_CONTROL_DTC_SETTING, 0x02])), bytes([0xC5, 0x02])
        )
        self.assertFalse(self.state.dtc_setting_enabled)

        self.send(bytes([SID_SESSION, uds_types.SESSION_EXTENDED]))

        self.assertFalse(
            self.state.dtc_setting_enabled,
            "会话切换不得重置 dtc_setting_enabled（那是 ECUReset 的语义）",
        )

    def test_did_override_survives_session_switch(self):
        """``0x2E`` 写入的 DID override 在切会话后仍在（只有复位才清）。"""
        unlock_extended(self.state)
        self.send(bytes([SID_WRITE, 0xF1, 0x02]) + b"KEEPIT")
        self.assertEqual(
            self.send(bytes([SID_READ, 0xF1, 0x02])),
            bytes([POS_READ, 0xF1, 0x02]) + b"KEEPIT",
        )

        self.send(bytes([SID_SESSION, uds_types.SESSION_DEFAULT]))

        self.assertEqual(
            self.send(bytes([SID_READ, 0xF1, 0x02])),
            bytes([POS_READ, 0xF1, 0x02]) + b"KEEPIT",
            "会话切换不得清除 DID override",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
