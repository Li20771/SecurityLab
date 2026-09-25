#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 2 **Step 1：执行框架** 单元测试

范围严格限定为"框架引入"本身，**不迁移任何服务**：

1. ``resolve_service_spec()`` 的解析契约（设计文档 §6.5，OD-4 = A′）；
2. ``_SPEC_EXECUTION`` / ``_guards_for()`` 的空白名单行为（§15.1）；
3. dispatcher 接入框架后**行为逐字节不变**（§20.2 第 2 步）。

**本文件不启用任何 guard**：``_SPEC_EXECUTION`` 必须保持 ``frozenset()``。
0x22 / 0x2E 的迁移属 Migration A / B，**尚未开始**。

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md``

运行：

    python3 tests/test_c1_phase2_framework_unit.py
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
from uds_server import (  # noqa: E402
    _SPEC_EXECUTION,
    SERVICE_SPECS,
    SERVICES,
    EcuState,
    _guards_for,
    handle_uds_request,
    resolve_service_spec,
)

SID_READ = 0x22
SID_WRITE = 0x2E
SID_SECURITY_ACCESS = 0x27
SID_TESTER_PRESENT = 0x3E
SID_UNKNOWN = 0x99


def custom_handler(request: bytes, state: EcuState) -> bytes:
    """可识别的自定义 handler（返回非 UDS 形态，便于断言"它确实被调用了"）。"""
    return bytes([0xAA, 0xBB])


class TestSpecExecutionWhitelistEmpty(unittest.TestCase):
    """
    §15.1 —— Step 1 的白名单**必须为空**，且 guards **一个都不执行**。
    """

    def test_whitelist_is_empty_frozenset(self):
        self.assertIsInstance(_SPEC_EXECUTION, frozenset)
        self.assertEqual(_SPEC_EXECUTION, frozenset())

    def test_no_service_is_enabled(self):
        """特别锁定：0x22 / 0x2E **不得**提前加入。"""
        self.assertNotIn(SID_READ, _SPEC_EXECUTION)
        self.assertNotIn(SID_WRITE, _SPEC_EXECUTION)
        self.assertEqual(len(_SPEC_EXECUTION), 0)

    def test_guards_for_returns_empty_for_every_modeled_service(self):
        """
        对 ``SERVICE_SPECS`` 中的**每一个** SID，``_guards_for()`` 都必须返回 ``()``
        —— 这是"Step 1 不执行任何 guard"的结构性证明。
        """
        self.assertTrue(SERVICE_SPECS, "前置条件：静态服务必须已被建模")
        for sid, spec in SERVICE_SPECS.items():
            with self.subTest(sid=hex(sid)):
                self.assertEqual(_guards_for(spec), ())

    def test_guards_for_returns_empty_even_when_spec_has_guards(self):
        """0x22 / 0x2E 的 spec **确实带有** guards —— 但白名单为空时仍不执行。"""
        for sid in (SID_READ, SID_WRITE):
            with self.subTest(sid=hex(sid)):
                self.assertTrue(SERVICE_SPECS[sid].guards, "该服务应已建模 guards")
                self.assertEqual(_guards_for(SERVICE_SPECS[sid]), ())


class TestResolveServiceSpecContract(unittest.TestCase):
    """§6.5 —— ``resolve_service_spec()`` 的五条解析契约。"""

    # ---- services is None ----

    def test_none_returns_modeled_spec(self):
        spec = resolve_service_spec(SID_READ, None)
        self.assertIsNotNone(spec)
        self.assertIs(spec, SERVICE_SPECS[SID_READ])

    def test_none_returns_none_for_unknown_sid(self):
        self.assertIsNone(resolve_service_spec(SID_UNKNOWN, None))

    def test_none_returns_none_for_security_access(self):
        """0x27 不在静态表内 → 未建模（OD-6 = 方案 A）。"""
        self.assertIsNone(resolve_service_spec(SID_SECURITY_ACCESS, None))

    def test_none_preserves_handler_identity(self):
        """``services=None`` 路径的 handler 必须**就是** ``SERVICES`` 中的对象。"""
        for sid in SERVICES:
            with self.subTest(sid=hex(sid)):
                spec = resolve_service_spec(sid, None)
                self.assertIs(spec.handler, SERVICES[sid])

    # ---- override：取代而非合并 ----

    def test_override_missing_sid_returns_none(self):
        """override 未列出的 SID → ``None``（→ dispatcher 返回 ``7F <sid> 11``）。"""
        override = {SID_TESTER_PRESENT: SERVICES[SID_TESTER_PRESENT]}
        self.assertIsNone(resolve_service_spec(SID_READ, override))

    def test_empty_override_makes_every_sid_unknown(self):
        self.assertIsNone(resolve_service_spec(SID_READ, {}))

    # ---- override：未建模 SID ----

    def test_override_unmodeled_sid_gets_empty_guards(self):
        """未建模 SID（0x27 注入 / 测试自定义 SID）→ ``guards=()``。"""
        spec = resolve_service_spec(SID_SECURITY_ACCESS, {SID_SECURITY_ACCESS: custom_handler})
        self.assertIsNotNone(spec)
        self.assertEqual(spec.sid, SID_SECURITY_ACCESS)
        self.assertIs(spec.handler, custom_handler)
        self.assertEqual(spec.guards, ())

    def test_override_custom_sid_gets_empty_guards(self):
        spec = resolve_service_spec(SID_UNKNOWN, {SID_UNKNOWN: custom_handler})
        self.assertEqual(spec.guards, ())
        self.assertIs(spec.handler, custom_handler)

    # ---- override：已建模 SID（OD-4 A′ 的核心）----

    def test_override_modeled_sid_replaces_handler_only(self):
        """
        A′ 的核心契约：对已建模 SID，``handler`` 来自 override，
        而 ``guards`` **仍来自 SERVICE_SPECS**（不得被清空）。
        """
        for sid in (SID_READ, SID_WRITE):
            with self.subTest(sid=hex(sid)):
                spec = resolve_service_spec(sid, {sid: custom_handler})
                self.assertIs(spec.handler, custom_handler, "handler 应来自 override")
                self.assertEqual(
                    spec.guards,
                    SERVICE_SPECS[sid].guards,
                    "guards 必须保留自 SERVICE_SPECS，不得被 override 清空",
                )
                self.assertTrue(spec.guards, "该服务本就带 guards")

    def test_override_0x22_keeps_its_single_guard(self):
        spec = resolve_service_spec(SID_READ, {SID_READ: custom_handler})
        self.assertIs(spec.handler, custom_handler)
        self.assertEqual(len(spec.guards), 1)
        self.assertEqual(spec.guards, SERVICE_SPECS[SID_READ].guards)

    def test_override_0x2E_keeps_its_six_guards(self):
        spec = resolve_service_spec(SID_WRITE, {SID_WRITE: custom_handler})
        self.assertIs(spec.handler, custom_handler)
        self.assertEqual(len(spec.guards), 6)
        self.assertEqual(spec.guards, SERVICE_SPECS[SID_WRITE].guards)

    def test_override_preserves_declarations(self):
        """``replace`` 只改 handler：其余声明字段（供矩阵/审计）必须原样保留。"""
        base = SERVICE_SPECS[SID_WRITE]
        spec = resolve_service_spec(SID_WRITE, {SID_WRITE: custom_handler})
        self.assertEqual(spec.sid, base.sid)
        self.assertEqual(spec.allowed_sessions, base.allowed_sessions)
        self.assertEqual(spec.required_security, base.required_security)
        self.assertEqual(spec.payload_scoped, base.payload_scoped)

    def test_unmodeled_override_spec_is_validated_shape(self):
        """未建模 SID 构造出的 spec 必须自洽（``payload_scoped=False`` + 空 guards）。"""
        spec = resolve_service_spec(SID_UNKNOWN, {SID_UNKNOWN: custom_handler})
        self.assertFalse(spec.payload_scoped)
        self.assertIsNone(spec.allowed_sessions)

    # ---- 纯净性 ----

    def test_resolution_does_not_call_any_guard(self):
        """
        ``resolve_service_spec`` 是**纯函数**：解析不得触发 guard。
        用一个会记录调用的 handler 证明解析期没有任何判定发生。
        """
        calls = []

        def recording_handler(request: bytes, state: EcuState) -> bytes:
            calls.append(request)
            return b""

        for services in (None, {SID_READ: recording_handler}, {SID_UNKNOWN: recording_handler}):
            for sid in (SID_READ, SID_UNKNOWN):
                resolve_service_spec(sid, services)

        self.assertEqual(calls, [], "解析阶段不得调用任何 handler")

    def test_resolution_is_pure_and_repeatable(self):
        services = {SID_READ: custom_handler}
        first = resolve_service_spec(SID_READ, services)
        second = resolve_service_spec(SID_READ, services)
        self.assertEqual(first, second)

    def test_resolution_does_not_mutate_state(self):
        state = EcuState()
        state.session = uds_types.SESSION_PROGRAMMING
        state.security_level = uds_types.SECURITY_LEVEL1
        before = (state.session, state.security_level, dict(state.did_overrides))
        resolve_service_spec(SID_WRITE, {SID_WRITE: custom_handler})
        after = (state.session, state.security_level, dict(state.did_overrides))
        self.assertEqual(before, after)


class TestDispatcherFrameworkIntegration(unittest.TestCase):
    """
    §20.2 第 2 步 —— 接入框架后 dispatcher **行为逐字节不变**。

    因为 ``_SPEC_EXECUTION`` 为空，guards 恒被跳过，handler 仍以既有方式取得。
    """

    def _requests(self):
        return (
            b"",
            bytes([0xFF]),
            bytes([SID_UNKNOWN]),
            bytes([SID_TESTER_PRESENT, 0x00]),
            bytes([SID_READ, 0xF1, 0x90]),
            bytes([SID_READ, 0xF1, 0x87]),
            bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF]),
            bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87]),
            bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
            bytes([SID_WRITE, 0xFF, 0xFF]) + b"X",
        )

    def _states(self):
        return (
            (uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
            (uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
            (uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            (uds_types.SESSION_PROGRAMMING, uds_types.SECURITY_LEVEL1),
        )

    def _state(self, session, security):
        state = EcuState()
        state.session = session
        state.security_level = security
        return state

    def test_dispatch_matches_legacy_handler_directly(self):
        """
        对每个 ``(请求, 初始状态)``，dispatcher 的响应必须**等于**
        直接调用 ``SERVICES[sid]`` handler 的响应 —— 即框架接入未改变执行结果。
        """
        for request in self._requests():
            for session, security in self._states():
                with self.subTest(request=request.hex(" "), session=hex(session)):
                    dispatched = handle_uds_request(
                        request, self._state(session, security)
                    )

                    if not request:
                        expected = uds_types.negative_response(
                            0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH
                        )
                    elif request[0] not in SERVICES:
                        expected = uds_types.negative_response(
                            request[0], uds_types.NRC_SERVICE_NOT_SUPPORTED
                        )
                    else:
                        expected = SERVICES[request[0]](
                            request, self._state(session, security)
                        )

                    self.assertEqual(dispatched, expected)

    def test_guards_are_not_executed_even_for_guarded_services(self):
        """
        Step 1 的关键行为证据：对带 guards 的已建模 SID 装自定义 handler 时，
        **guard 不会拦截它**（因为 ``_SPEC_EXECUTION`` 为空）。

        请求 ``22 F1 87`` 在 LOCKED 下会被 0x22 的 guard 判为 ``0x33``；
        但 Step 1 根本不执行 guard，故自定义 handler 仍被调用。

        .. note::

           **Migration A 会改变本用例** —— 按设计文档 §6.9，
           ``_SPEC_EXECUTION = {0x22}`` 之后该请求必须返回 ``7F 22 33``
           且 custom_handler 不得被调用。届时本用例应改写为断言"收紧已生效"。
        """
        state = self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED)
        response = handle_uds_request(
            bytes([SID_READ, 0xF1, 0x87]), state, services={SID_READ: custom_handler}
        )
        self.assertEqual(
            response,
            bytes([0xAA, 0xBB]),
            "Step 1 不得执行 guard：自定义 handler 必须被直接调用",
        )

    def test_override_semantics_unchanged(self):
        """N6 契约在框架接入后保持：取代而非合并；缺 SID → ``11``。"""
        override = {SID_TESTER_PRESENT: SERVICES[SID_TESTER_PRESENT]}
        self.assertEqual(
            handle_uds_request(
                bytes([SID_TESTER_PRESENT, 0x00]), EcuState(), services=override
            ),
            bytes([0x7E, 0x00]),
        )
        self.assertEqual(
            handle_uds_request(bytes([SID_READ, 0xF1, 0x90]), EcuState(), services=override),
            bytes([0x7F, SID_READ, uds_types.NRC_SERVICE_NOT_SUPPORTED]),
        )

    def test_explicit_static_table_is_equivalent_to_default(self):
        """显式传入 ``dict(SERVICES)`` 仍与默认路径逐字节等价。"""
        for request in self._requests():
            with self.subTest(request=request.hex(" ")):
                self.assertEqual(
                    handle_uds_request(request, EcuState()),
                    handle_uds_request(request, EcuState(), services=dict(SERVICES)),
                )

    def test_empty_request_precedes_any_lookup(self):
        self.assertEqual(
            handle_uds_request(b"", EcuState()),
            bytes([0x7F, 0x00, uds_types.NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_unregistered_sid_echoes_request_sid(self):
        for sid in (0x00, 0x99, 0xFF):
            with self.subTest(sid=hex(sid)):
                self.assertEqual(
                    handle_uds_request(bytes([sid, 0x01, 0x02]), EcuState()),
                    bytes([0x7F, sid, uds_types.NRC_SERVICE_NOT_SUPPORTED]),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
