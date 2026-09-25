#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 2 **执行框架 + 迁移阶段** 单元测试

覆盖：

1. ``resolve_service_spec()`` 的解析契约（设计文档 §6.5，OD-4 = A′）；
2. ``_SPEC_EXECUTION`` / ``_guards_for()`` 在**当前迁移阶段**的行为（§15）；
3. dispatcher 接入框架后与其 legacy 路径的**行为一致性**（§20.2）；
4. **定向激活证据**：0x22（A1）与 0x2E（B1）的 guard 确已接管。

**当前阶段：B1（Migration B）** —— 0x22 与 0x2E 都走 ServiceSpec 执行路径：

```
_SPEC_EXECUTION = {0x22, 0x2E}     0x22 = ON
                                   0x2E = ON
                                   0x27 = 不受影响（不在静态表，见 SPECS_EXCLUDED）
```

阶段推进时必须**同时**更新 ``uds_server._SPEC_EXECUTION`` 与
本文件的 ``EXPECTED_SPEC_EXECUTION`` —— 使"某个 SID 被启用/停用"永远是一次
**可见的、可 review 的**源码变更（§15.3：拒绝 feature flag / 环境变量切换）。

完整 golden corpus 的逐字节对拍属 **A2 / B2**，由
``test_c1_phase2_a0_golden.py`` / （B2 起）0x2E 侧承担，本文件**不**重复它。

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


#: 当前迁移阶段（设计 §11.1 / §12.1，**严格 A → B 顺序**）。
#:
#:     A1  启用 0x22 的 spec execution            production 路径**改变**
#:     A2  对 0x22 做 92 条 golden 逐字节对拍     production 路径**不变**
#:     B1  启用 0x2E 的 spec execution            production 路径**改变**
#:
#: **阶段类型决定了白名单该不该动**：
#:
#: * ``A1 → A2`` 是**验证阶段**推进 → ``EXPECTED_SPEC_EXECUTION`` **不变**；
#: * ``A2 → B1`` 是**执行阶段**推进 → ``EXPECTED_SPEC_EXECUTION`` **必须新增 0x2E**。
#:
#: 无论哪种，两者都必须**同时**改动 —— 使"某个 SID 被启用/停用"永远是一次
#: 可见的、可 review 的源码变更（§15.3：拒绝 feature flag / 环境变量切换）。
MIGRATION_STAGE = "B1"

#: 该阶段下 ``uds_server._SPEC_EXECUTION`` 的**期望内容**。
#:
#: 这里是**显式声明**，而不是"读取当前值再断言它自洽" —— 任何启用或停用
#: 都必须同时改动 production 常量与本常量，从而在 diff 中**可见**。
EXPECTED_SPEC_EXECUTION = frozenset({SID_READ, SID_WRITE})


class TestSpecExecutionWhitelist(unittest.TestCase):
    """
    §15 —— ``_SPEC_EXECUTION`` 是**源码常量白名单**（非 feature flag），
    决定哪些 SID 的 guards 真正被求值。

    本类断言的是**当前阶段**（``MIGRATION_STAGE``）的白名单内容。
    """

    def test_whitelist_matches_declared_stage(self):
        self.assertIsInstance(_SPEC_EXECUTION, frozenset)
        self.assertEqual(
            _SPEC_EXECUTION,
            EXPECTED_SPEC_EXECUTION,
            f"当前阶段 {MIGRATION_STAGE} 的白名单与声明不符",
        )

    def test_0x22_is_enabled(self):
        """Migration A1：0x22 走 ServiceSpec 执行路径。"""
        self.assertIn(SID_READ, _SPEC_EXECUTION)

    def test_0x2E_is_now_enabled(self):
        """**B1：0x2E 走 ServiceSpec 执行路径**（Migration B 的启用阶段）。"""
        self.assertIn(SID_WRITE, _SPEC_EXECUTION)

    def test_0x27_is_not_in_the_whitelist(self):
        """0x27 不在静态表内（OD-6 = 方案 A），也不得进入白名单。"""
        self.assertNotIn(SID_SECURITY_ACCESS, _SPEC_EXECUTION)

    def test_guards_are_executed_for_enabled_services(self):
        """已启用 SID 的 guards 必须真的被求值（返回其完整 guard 链）。"""
        self.assertTrue(SERVICE_SPECS[SID_READ].guards, "0x22 应已建模 guard")
        self.assertEqual(
            _guards_for(SERVICE_SPECS[SID_READ]), SERVICE_SPECS[SID_READ].guards
        )

    def test_guards_are_skipped_for_every_disabled_service(self):
        """
        未启用 SID 的 guards 必须恒被跳过 —— 结构性证明：
        除白名单外的**每一个**已建模 SID，``_guards_for()`` 都返回 ``()``。
        """
        disabled = [sid for sid in SERVICE_SPECS if sid not in EXPECTED_SPEC_EXECUTION]
        self.assertTrue(disabled, "应当仍有未迁移的服务")
        for sid in disabled:
            with self.subTest(sid=hex(sid)):
                self.assertEqual(_guards_for(SERVICE_SPECS[sid]), ())

    def test_0x2E_guards_are_executed(self):
        """B1：0x2E 的 **6 个 guard 全部**被求值（顺序即语义，见 §12.4）。"""
        self.assertEqual(len(SERVICE_SPECS[SID_WRITE].guards), 6)
        self.assertEqual(
            _guards_for(SERVICE_SPECS[SID_WRITE]),
            SERVICE_SPECS[SID_WRITE].guards,
        )


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
    §20.2 —— dispatcher 接入框架后，其可观测行为与其 legacy 路径一致。

    A1 起 0x22 会先经 guards；因为 guard 链是既有 inline 检查的**忠实模型**
    （Phase 1 已由 28 例 model-vs-legacy 证明），故最终响应仍逐字节相同。
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

    def test_override_does_not_disable_guard_for_enabled_service(self):
        """
        A1（原 Step 1 用例的**语义翻转**）：对**已启用**的已建模 SID 装自定义
        handler 时，guard 仍先执行并短路 —— ``custom_handler`` **不得被调用**。

        这正是 OD-4 A′ 的关键安全语义：``services=`` 替换**实现**，不替换**授权**。
        （Step 1 时白名单为空，guard 被跳过，故当时本用例断言相反结果。）
        """
        state = self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED)
        response = handle_uds_request(
            bytes([SID_READ, 0xF1, 0x87]), state, services={SID_READ: custom_handler}
        )
        self.assertEqual(
            response,
            bytes([0x7F, SID_READ, uds_types.NRC_SECURITY_ACCESS_DENIED]),
            "guard 必须先执行并短路；custom_handler 不得被调用",
        )

    def test_still_disabled_service_skips_guards_and_calls_override(self):
        """
        B1 之后 0x22 / 0x2E **都已启用**，故反向对照改用**仍未迁移**的服务：
        其 guards 不执行，override 的自定义 handler 被**直接调用**。

        （Phase 1 时"带 guards 但未启用"的服务只有 0x22 / 0x2E 两个；
        B1 之后该组合已不存在，因此改用 guards 为空 SID 做机制层面的对照。）
        """
        self.assertNotIn(SID_TESTER_PRESENT, EXPECTED_SPEC_EXECUTION)
        state = self._state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED)
        response = handle_uds_request(
            bytes([SID_TESTER_PRESENT, 0x00]),
            state,
            services={SID_TESTER_PRESENT: custom_handler},
        )
        self.assertEqual(
            response,
            bytes([0xAA, 0xBB]),
            "未迁移服务的 guards 不得执行：自定义 handler 必须被直接调用",
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


class TestA1_0x22GuardActivation(unittest.TestCase):
    """
    §11.1 **A1 定向激活证据** —— 0x22 的 guard 已经真正接管执行路径。

    这是 A1 的**完成判据**：最小、定向、可读的行为证据。

    完整 golden corpus（92 条）的逐字节对拍属 **A2**，由
    ``test_c1_phase2_a0_golden.py`` 承担，本类**不**重复它。
    """

    def _state(self, session, security):
        state = EcuState()
        state.session = session
        state.security_level = security
        return state

    # ---- 1. guard 确实拦截 ----

    def test_guard_blocks_protected_did_while_locked(self):
        """``22 F1 87`` + EXTENDED + LOCKED → ``7F 22 33``（guard 接管）。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_READ, 0xF1, 0x87]),
                self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
            ),
            bytes([0x7F, SID_READ, uds_types.NRC_SECURITY_ACCESS_DENIED]),
        )

    def test_guard_blocks_unknown_did(self):
        """``22 FF FF`` → ``7F 22 31``（guard 的未知 DID 分支）。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_READ, 0xFF, 0xFF]),
                self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            ),
            bytes([0x7F, SID_READ, uds_types.NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_guard_blocks_malformed_length(self):
        """``22``（长度 1）→ ``7F 22 13``（guard 的格式分支）。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_READ]),
                self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            ),
            bytes([0x7F, SID_READ, uds_types.NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    # ---- 2. guard 放行后 handler 执行 ----

    def test_guard_passes_then_handler_returns_data(self):
        """``22 F1 87`` + LEVEL1 → guard 放行 → handler 返回正响应。"""
        response = handle_uds_request(
            bytes([SID_READ, 0xF1, 0x87]),
            self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
        )
        self.assertEqual(response[0], 0x62)
        self.assertEqual(response[1:3], bytes([0xF1, 0x87]))

    def test_guard_passes_for_public_did_while_locked(self):
        """公开 DID 在 LOCKED 下也应放行（guard 不误伤）。"""
        response = handle_uds_request(
            bytes([SID_READ, 0xF1, 0x90]),
            self._state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
        )
        self.assertEqual(response[0], 0x62)

    # ---- 3. 冻结锚点在 guard 接管后仍成立 ----

    def test_frozen_anchor_protected_first_still_security_denied(self):
        """``22 F1 87 FF FF`` → ``7F 22 33``（顺序语义未被改变）。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF]),
                self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
            ),
            bytes([0x7F, SID_READ, uds_types.NRC_SECURITY_ACCESS_DENIED]),
        )

    def test_frozen_anchor_unknown_first_still_out_of_range(self):
        """``22 FF FF F1 87`` → ``7F 22 31``（first-failure-wins 未被改变）。"""
        self.assertEqual(
            handle_uds_request(
                bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87]),
                self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
            ),
            bytes([0x7F, SID_READ, uds_types.NRC_REQUEST_OUT_OF_RANGE]),
        )

    # ---- 4. OD-4 A′：override 不能绕过授权，也不会丢失授权 ----

    def test_override_handler_runs_only_after_guard_passes(self):
        """guard 放行后，override 的 handler 才被调用（替换实现，不替换授权）。"""
        response = handle_uds_request(
            bytes([SID_READ, 0xF1, 0x87]),
            self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            services={SID_READ: custom_handler},
        )
        self.assertEqual(response, bytes([0xAA, 0xBB]))

    def test_ecu_style_full_table_override_still_executes_guards(self):
        """
        复刻 ``ecu.py:374`` 的注入形态 ``{**SERVICES, ...}``：
        0x22 的 handler 虽来自 override，guards 仍须生效
        —— 即设计 §6.3 的"路径分叉"已被 A′ 消除。
        """
        self.assertEqual(
            handle_uds_request(
                bytes([SID_READ, 0xF1, 0x87]),
                self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
                services=dict(SERVICES),
            ),
            bytes([0x7F, SID_READ, uds_types.NRC_SECURITY_ACCESS_DENIED]),
        )

    # ---- 5. 0x27 不受影响 ----

    def test_0x27_is_still_not_resolvable_from_the_static_path(self):
        """
        0x27 不在静态表内（OD-6 = 方案 A）：``resolve(0x27, None) is None``。
        A1 只改白名单，未触碰 0x27 的注入边界。
        """
        self.assertIsNone(resolve_service_spec(SID_SECURITY_ACCESS, None))

    def test_0x27_injected_handler_still_has_no_guards(self):
        """0x27 由入口层注入时仍归一化为 ``guards=()``（不受 A1 影响）。"""
        spec = resolve_service_spec(SID_SECURITY_ACCESS, {SID_SECURITY_ACCESS: custom_handler})
        self.assertIsNotNone(spec)
        self.assertEqual(spec.guards, ())
        self.assertIs(spec.handler, custom_handler)


class TestB1_0x2EGuardActivation(unittest.TestCase):
    """
    §12.1 **B1 定向激活证据** —— 0x2E 的 guard 已经真正接管执行路径。

    这是 B1 的**完成判据**：最小、定向、可读的行为证据。

    完整 golden corpus（70 条）的 legacy ↔ spec 逐字节对拍属 **B2**，
    本类**不**重复它。
    """

    def _state(self, session, security):
        state = EcuState()
        state.session = session
        state.security_level = security
        return state

    def _dispatch(self, request, session, security, **kwargs):
        return handle_uds_request(request, self._state(session, security), **kwargs)

    # ---- 8.1 / 8.2 guard 链本身 ----

    def test_0x2E_spec_has_six_guards(self):
        self.assertEqual(len(SERVICE_SPECS[SID_WRITE].guards), 6)

    def test_guards_for_returns_the_full_chain(self):
        self.assertEqual(
            _guards_for(SERVICE_SPECS[SID_WRITE]),
            SERVICE_SPECS[SID_WRITE].guards,
        )

    # ---- 8.3 ~ 8.8 每一道 rung 的定向接管证据 ----

    def test_rung1_length_guard_takes_over(self):
        """``2E`` → ``7F 2E 13``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE]), uds_types.SESSION_EXTENDED,
                           uds_types.SECURITY_LEVEL1),
            bytes([0x7F, SID_WRITE, uds_types.NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_rung1_length_guard_takes_over_for_short_did(self):
        """``2E F1`` → ``7F 2E 13``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1]), uds_types.SESSION_EXTENDED,
                           uds_types.SECURITY_LEVEL1),
            bytes([0x7F, SID_WRITE, uds_types.NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    def test_rung2_unknown_did_guard_takes_over(self):
        """``2E FF FF 58`` → ``7F 2E 31``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xFF, 0xFF]) + b"X",
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            bytes([0x7F, SID_WRITE, uds_types.NRC_REQUEST_OUT_OF_RANGE]),
        )

    def test_rung3_default_session_guard_takes_over(self):
        """``2E F1 02 58`` + DEFAULT → ``7F 2E 7F``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                           uds_types.SESSION_DEFAULT, uds_types.SECURITY_LEVEL1),
            bytes([0x7F, SID_WRITE, uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]),
        )

    def test_rung4_security_guard_takes_over(self):
        """``2E F1 02 58`` + EXTENDED + LOCKED → ``7F 2E 33``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
            bytes([0x7F, SID_WRITE, uds_types.NRC_SECURITY_ACCESS_DENIED]),
        )

    def test_rung5_writability_guard_takes_over(self):
        """``2E F1 90 58``（已知只读 DID）+ EXTENDED + LEVEL1 → ``7F 2E 22``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x90]) + b"X",
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            bytes([0x7F, SID_WRITE, uds_types.NRC_CONDITIONS_NOT_CORRECT]),
        )

    def test_rung6_data_length_guard_takes_over(self):
        """``2E F1 02`` + 17 字节（超出闭区间上界 16）→ ``7F 2E 13``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X" * 17,
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            bytes([0x7F, SID_WRITE, uds_types.NRC_INCORRECT_MESSAGE_LENGTH]),
        )

    # ---- 8.9 全链通过 → handler 执行 → 真实写入副作用 ----

    def test_all_guards_pass_then_handler_writes(self):
        """
        六道 rung 全部通过后，**legacy handler 正常执行并产生真实写入副作用**。

        这一条把 `guard chain pass` 与 `handler side effect` 连起来 ——
        仅断言响应字节不足够（§14：0x2E 是写服务）。
        """
        state = self._state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1)
        response = handle_uds_request(
            bytes([SID_WRITE, 0xF1, 0x02]) + b"X", state
        )
        self.assertEqual(response, bytes([0x6E, 0xF1, 0x02]))
        self.assertEqual(state.did_overrides[0xF102], b"X")

    # ---- §9 冻结 rung 顺序 ----

    def test_frozen_rung_order_is_preserved(self):
        """
        ``13 → 31 → 7F → 33 → 22 → 13`` 的**优先关系**在 spec 路径下逐条成立。

        每条用例都被构造为"只应在该 rung 失败"，因此 NRC 直接反映 rung 顺序。
        """
        cases = [
            (bytes([SID_WRITE]), uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x13, "① 长度"),
            (bytes([SID_WRITE, 0xFF, 0xFF]) + b"X", uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x31, "② 未知 DID 先于 ③ 会话"),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X", uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x7F, "③ DEFAULT 会话先于 ④ 安全"),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X", uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LOCKED, 0x33, "④ 安全先于 ⑤ 可写性"),
            (bytes([SID_WRITE, 0xF1, 0x90]) + b"X", uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LEVEL1, 0x22, "⑤ 可写性先于 ⑥ 数据长度"),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X" * 17, uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LEVEL1, 0x13, "⑥ 数据长度"),
        ]
        for request, session, security, expected, label in cases:
            with self.subTest(rung=label):
                response = self._dispatch(request, session, security)
                self.assertEqual(
                    response,
                    bytes([0x7F, SID_WRITE, expected]),
                    f"{label} 的 NRC 与冻结 rung 顺序不符",
                )

    # ---- §10 OD-4 A′：override 不可绕过 0x2E guards ----

    def test_override_cannot_bypass_guard_in_default_session(self):
        """``services={0x2E: custom}`` + DEFAULT → 仍 ``7F 2E 7F``，custom 不被调用。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                           uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED,
                           services={SID_WRITE: custom_handler}),
            bytes([0x7F, SID_WRITE, uds_types.NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]),
        )

    def test_override_cannot_bypass_guard_while_locked(self):
        """``services={0x2E: custom}`` + EXTENDED + LOCKED → 仍 ``7F 2E 33``。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED,
                           services={SID_WRITE: custom_handler}),
            bytes([0x7F, SID_WRITE, uds_types.NRC_SECURITY_ACCESS_DENIED]),
        )

    def test_override_handler_runs_only_after_guards_pass(self):
        """guards 全过 → override 的 handler 才被调用（替换实现，不替换授权）。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1,
                           services={SID_WRITE: custom_handler}),
            bytes([0xAA, 0xBB]),
        )

    def test_ecu_style_full_table_override_still_executes_0x2E_guards(self):
        """复刻 ``ecu.py:374`` 的 ``{**SERVICES, ...}`` 注入形态：guards 仍生效。"""
        self.assertEqual(
            self._dispatch(bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                           uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED,
                           services=dict(SERVICES)),
            bytes([0x7F, SID_WRITE, uds_types.NRC_SECURITY_ACCESS_DENIED]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
