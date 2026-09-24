#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 1 服务规格模型（``service_spec``）单元测试

覆盖 C1 设计冻结文档（``docs/OQ_C1_PERMISSION_SESSION_DESIGN_REVIEW_2026-09-24.md``）
要求的 Phase 1 三类检查：

1. **模型结构**（§10 / §11 / §18.0 OD-2）：全部已实现服务都被建模或**显式排除**；
   ``payload_scoped=True → guards 非空``；只有真实具备授权语义的服务被标记
   payload-scoped；其余 12 个服务 ``guards=()`` 且**不得**被新增 session/security 限制。
2. **声明一致性 / conformance**（§11）：非 payload-scoped 服务的声明必须能完整
   描述其授权语义（遍历 ``session × security_level`` 全组合逐格对拍）；
   payload-scoped 服务**不得**被压成简单矩阵，只验证"标记 + guards 非空 + 不假装完整"。
3. **模型 vs legacy 一致性**（§13）：Phase 1 不切换执行路径，因此必须证明
   **模型对 legacy 行为的解释与 legacy 实际响应逐字节一致** —— 这是 Phase 2
   迁移的对拍基准。

**Phase 1 不执行 guards**：dispatcher（``handle_uds_request``）仍走
``SERVICES[sid] -> handler``。本文件只读模型 + 对拍，**不**改变任何执行路径。

运行：

    python3 tests/test_c1_service_spec_unit.py
或：
    bash tests/run_tests.sh unit
"""

import itertools
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import service_spec  # noqa: E402
import uds_types  # noqa: E402
from did_database import DID_DATABASE, PROTECTED_DIDS, WRITABLE_DIDS  # noqa: E402
from uds_server import (  # noqa: E402
    SERVICE_SPECS,
    SERVICES,
    SPECS_EXCLUDED,
    EcuState,
    handle_uds_request,
)

SID_READ = 0x22
SID_WRITE = 0x2E
SID_SECURITY_ACCESS = 0x27

DID_WRITABLE = 0xF102
DID_READONLY = 0xF190
DID_PROTECTED = 0xF187
DID_UNKNOWN = 0xFFFF

SESSIONS = (
    uds_types.SESSION_DEFAULT,
    uds_types.SESSION_EXTENDED,
    uds_types.SESSION_PROGRAMMING,
)
SECURITY_LEVELS = (uds_types.SECURITY_LOCKED, uds_types.SECURITY_LEVEL1)

#: 当前**只有**这两个服务具备真实授权语义（设计文档 §2.4 / §8.1）。
AUTHORIZATION_SERVICES = frozenset({SID_READ, SID_WRITE})

#: 有子功能/控制字节、但**没有**授权门控的其余服务（设计文档 §8.1 的 12 个）。
NON_AUTHORIZATION_SERVICES = frozenset(set(SERVICES) - AUTHORIZATION_SERVICES)


def make_state(session: int, security: int) -> EcuState:
    state = EcuState()
    state.session = session
    state.security_level = security
    return state


def evaluate_guards(spec, request: bytes, state: EcuState):
    """
    按序求值 guard 链（**只读**，不修改状态）。

    :returns: ``(nrc | None, 求值过的 guard 数)``；``nrc`` 非空表示被拒。
    """
    for index, guard in enumerate(spec.guards):
        nrc = guard(request, state)
        if nrc is not None:
            return nrc, index + 1
    return None, len(spec.guards)


def legacy_response(sid: int, request: bytes, state: EcuState) -> bytes:
    """legacy 执行路径（Phase 1 的权威实现）。"""
    assert request[0] == sid
    return handle_uds_request(request, state)


class TestModelStructure(unittest.TestCase):
    """§10 / §11 —— 模型与覆盖关系。"""

    def test_every_static_service_is_modelled(self):
        """静态注册表的每个服务都必须有 spec（且 sid 与键一致）。"""
        self.assertEqual(set(SERVICE_SPECS), set(SERVICES))
        for sid, spec in SERVICE_SPECS.items():
            with self.subTest(sid=hex(sid)):
                self.assertEqual(spec.sid, sid)

    def test_0x27_is_explicitly_excluded_not_silently_missing(self):
        """
        ``0x27`` 是**已知的注入型服务**：不在静态表、也不在 spec 内，
        但必须**显式**出现在 ``SPECS_EXCLUDED`` 中（附原因），
        以免被误读成"漏建模"。
        """
        self.assertNotIn(SID_SECURITY_ACCESS, SERVICE_SPECS)
        self.assertIn(SID_SECURITY_ACCESS, SPECS_EXCLUDED)
        self.assertTrue(SPECS_EXCLUDED[SID_SECURITY_ACCESS].strip())

    def test_specs_and_exclusions_are_disjoint(self):
        self.assertFalse(set(SERVICE_SPECS) & set(SPECS_EXCLUDED))

    def test_only_real_authorization_services_are_payload_scoped(self):
        """当前只有 0x22 / 0x2E 具备授权语义 → 只有它们可被标记 payload_scoped。"""
        scoped = {sid for sid, spec in SERVICE_SPECS.items() if spec.payload_scoped}
        self.assertEqual(scoped, set(AUTHORIZATION_SERVICES))

    def test_non_authorization_services_have_no_guards(self):
        """
        其余 12 个服务**不得**有授权 guard —— 它们当前没有 session/security 限制，
        新增即行为变更（设计文档 §8.3 明确禁止"看起来更完整"的补丁）。
        """
        for sid in sorted(NON_AUTHORIZATION_SERVICES):
            with self.subTest(sid=hex(sid)):
                spec = SERVICE_SPECS[sid]
                self.assertEqual(spec.guards, ())
                self.assertFalse(spec.payload_scoped)
                self.assertIsNone(spec.allowed_sessions, "不得新增会话约束")
                self.assertEqual(
                    spec.required_security,
                    uds_types.SECURITY_LOCKED,
                    "不得新增安全等级约束",
                )

    def test_0x22_declaration(self):
        """``0x22``：per-DID 权限 → ``required_security=None``；单 guard。"""
        spec = SERVICE_SPECS[SID_READ]
        self.assertTrue(spec.payload_scoped)
        self.assertIsNone(spec.required_security)
        self.assertEqual(len(spec.guards), 1)
        self.assertIsNone(spec.allowed_sessions)

    def test_0x2E_declaration(self):
        """``0x2E``：整体要求 LEVEL1（合法声明）+ 六级链；仍标 payload_scoped。"""
        spec = SERVICE_SPECS[SID_WRITE]
        self.assertTrue(spec.payload_scoped)
        self.assertEqual(spec.required_security, uds_types.SECURITY_LEVEL1)
        self.assertEqual(len(spec.guards), 6)
        self.assertIsNone(spec.allowed_sessions)

    def test_spec_is_immutable(self):
        """spec 为 frozen dataclass：模型不得被运行时改写。"""
        spec = SERVICE_SPECS[SID_READ]
        with self.assertRaises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError
            spec.payload_scoped = False  # type: ignore[misc]

    def test_model_is_not_executed_by_the_dispatcher(self):
        """
        结构性证据：Phase 1 的 dispatcher **不引用** ``SERVICE_SPECS``。

        （行为证据见 ``TestModelAgreesWithLegacy``：即便把 spec 整体置空，
        响应也不会变 —— 但这里用更直接的方式：检查 dispatcher 源码不引用它。）
        """
        with open(os.path.join(_ECU_DIR, "uds_server.py"), encoding="utf-8") as handle:
            source = handle.read()

        dispatcher = source.split("def handle_uds_request(", 2)[-1]
        self.assertNotIn(
            "SERVICE_SPECS",
            dispatcher,
            "Phase 1 的 dispatcher 不得引用 SERVICE_SPECS（切换执行路径属 Phase 2）",
        )


class TestStructuralValidation(unittest.TestCase):
    """§18.0 OD-2 —— ``validate_specs`` 的结构规则（含负例）。"""

    def _spec(self, **kwargs):
        base = dict(
            sid=0x99,
            handler=lambda request, state: b"",
        )
        base.update(kwargs)
        return service_spec.ServiceSpec(**base)

    def test_valid_specs_pass(self):
        service_spec.validate_specs(SERVICE_SPECS)

    def test_payload_scoped_requires_non_empty_guards(self):
        """R1：``payload_scoped=True`` 而 ``guards`` 为空 → 拒绝。"""
        spec = self._spec(payload_scoped=True, guards=())
        with self.assertRaises(service_spec.SpecError):
            service_spec.validate_specs({0x99: spec})

    def test_non_payload_scoped_may_have_empty_guards(self):
        """R1 的**方向性**：``guards=()`` 且非 payload-scoped → 合法。"""
        spec = self._spec(payload_scoped=False, guards=())
        service_spec.validate_specs({0x99: spec})

    def test_guard_must_be_callable(self):
        """R2。"""
        spec = self._spec(payload_scoped=True, guards=(None,))  # type: ignore[arg-type]
        with self.assertRaises(service_spec.SpecError):
            service_spec.validate_specs({0x99: spec})

    def test_empty_allowed_sessions_rejected(self):
        """R3：空集合必须用 ``None`` 表达。"""
        spec = self._spec(allowed_sessions=frozenset())
        with self.assertRaises(service_spec.SpecError):
            service_spec.validate_specs({0x99: spec})

    def test_unknown_session_rejected(self):
        """R3：未知会话。"""
        spec = self._spec(allowed_sessions=frozenset({0x7F}))
        with self.assertRaises(service_spec.SpecError):
            service_spec.validate_specs({0x99: spec})

    def test_unknown_security_level_rejected(self):
        """R4：未知安全等级。"""
        spec = self._spec(required_security=0x7F)
        with self.assertRaises(service_spec.SpecError):
            service_spec.validate_specs({0x99: spec})

    def test_sid_key_mismatch_rejected(self):
        """R5：防装配错位。"""
        spec = self._spec(sid=0x98)
        with self.assertRaises(service_spec.SpecError):
            service_spec.validate_specs({0x99: spec})


class TestDeclarationConformance(unittest.TestCase):
    """§11 —— 按服务类别分别验证声明与真实授权语义一致。"""

    def test_non_payload_scoped_declarations_match_behaviour(self):
        """
        非 payload-scoped 服务：遍历 ``session × security_level`` **全组合**，
        其 guard 链的实际 allow/deny 必须与声明一致。

        这些服务 ``guards=()``（链恒放行）+ 声明"无会话约束 / LEVEL0" →
        对**所有**组合都应放行，且声明不得声称任何限制。
        """
        for sid in sorted(NON_AUTHORIZATION_SERVICES):
            spec = SERVICE_SPECS[sid]
            for session, security in itertools.product(SESSIONS, SECURITY_LEVELS):
                with self.subTest(sid=hex(sid), session=hex(session), security=security):
                    state = make_state(session, security)
                    nrc, _ = evaluate_guards(spec, bytes([sid]), state)

                    declared_unrestricted = (
                        spec.allowed_sessions is None
                        and spec.required_security == uds_types.SECURITY_LOCKED
                    )
                    self.assertTrue(declared_unrestricted)
                    self.assertIsNone(
                        nrc, "声明为无授权约束的服务，其 guard 链不得拒绝"
                    )

    def test_payload_scoped_declarations_are_not_flattened(self):
        """
        payload-scoped 服务：**不得**压成简单矩阵；只验证
        ① 标记为 True ② guards 非空 ③ 声明没有"假装完整"。
        """
        for sid in sorted(AUTHORIZATION_SERVICES):
            with self.subTest(sid=hex(sid)):
                spec = SERVICE_SPECS[sid]
                self.assertTrue(spec.payload_scoped)
                self.assertTrue(spec.guards, "payload-scoped 服务必须有 guards")

    def test_0x22_does_not_pretend_to_be_fully_described(self):
        """
        ``0x22`` 的权限是 per-DID → 它**不得**声明一个具体 ``required_security``
        来"假装完整"（``None`` 是唯一诚实的取值）。
        """
        self.assertIsNone(SERVICE_SPECS[SID_READ].required_security)

    def test_0x22_permission_really_is_per_did(self):
        """
        用**同一状态下的两个 DID** 证明 per-DID 语义确实存在 —— 这正是
        ``required_security`` 无法用单一值概括的原因。
        """
        state = make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED)

        public = legacy_response(SID_READ, bytes([SID_READ, 0xF1, 0x90]), state)
        self.assertEqual(public[0], 0x62, "公开 DID 应可读")

        protected = legacy_response(SID_READ, bytes([SID_READ, 0xF1, 0x87]), state)
        self.assertEqual(protected, bytes([0x7F, SID_READ, 0x33]))

        self.assertIn(DID_READONLY, DID_DATABASE)
        self.assertIn(DID_PROTECTED, PROTECTED_DIDS)
        self.assertNotIn(DID_PROTECTED, WRITABLE_DIDS)


class TestGuardChainOrder(unittest.TestCase):
    """§13 —— guard **顺序**必须与冻结链逐级一致。"""

    def test_0x2E_guard_chain_is_the_frozen_six_rung_order(self):
        """
        ``0x2E`` 的六级链：``13 → 31 → 7F → 33 → 22 → 13``。

        逐个构造"只在该级失败"的请求，断言**被哪个 guard 拒绝**（而不是只看 NRC 值）
        —— 这才真正锁住顺序。
        """
        spec = SERVICE_SPECS[SID_WRITE]

        cases = [
            (0, bytes([0x2E, 0xF1]), "① len<4 → 13"),
            (1, bytes([0x2E, 0xFF, 0xFF]) + b"X", "② 未知 DID → 31（默认会话下也先于此）"),
            (2, bytes([0x2E, 0xF1, 0x02]) + b"X", "③ 默认会话 → 7F"),
            (3, bytes([0x2E, 0xF1, 0x02]) + b"X", "④ 未解锁 → 33"),
            (4, bytes([0x2E, 0xF1, 0x90]) + b"X", "⑤ 只读 DID → 22"),
            (5, bytes([0x2E, 0xF1, 0x02]) + b"X" * 17, "⑥ 数据过长 → 13"),
        ]
        states = [
            make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
            make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
            make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
            make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
            make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
            make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
        ]

        for (expected_index, request, label), state in zip(cases, states, strict=True):
            with self.subTest(rung=label):
                nrc, evaluated = evaluate_guards(spec, request, state)
                self.assertIsNotNone(nrc, f"{label} 应被拒绝")
                self.assertEqual(
                    evaluated,
                    expected_index + 1,
                    f"{label} 必须由第 {expected_index + 1} 个 guard 拒绝"
                    f"（实际由第 {evaluated} 个）",
                )

        # 全部通过 → 链返回 None
        ok_request = bytes([0x2E, 0xF1, 0x02]) + b"OK"
        ok_state = make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1)
        nrc, evaluated = evaluate_guards(spec, ok_request, ok_state)
        self.assertIsNone(nrc)
        self.assertEqual(evaluated, 6)


class TestModelAgreesWithLegacy(unittest.TestCase):
    """
    §13 —— **模型 vs legacy** 的一致性（Phase 1 的核心验证）。

    Phase 1 不让 0x22/0x2E 经 guards 执行，因此必须换一种方式证明模型是**忠实**的：
    对同一 ``(请求, 初始状态)``，把 **guard 链的判定**与 **legacy handler 的实际响应**
    逐字节对拍：

        guard 链返回 NRC  → legacy 必须返回 ``7F <sid> <nrc>``
        guard 链返回 None → legacy 必须返回**正响应**

    这个等价关系就是 Phase 2 迁移的对拍基准。
    """

    def _assert_agreement(self, sid: int, request: bytes, state: EcuState) -> bytes:
        spec = SERVICE_SPECS[sid]
        nrc, _ = evaluate_guards(spec, request, state)
        response = legacy_response(sid, request, state)

        if nrc is not None:
            self.assertEqual(
                response,
                bytes([0x7F, sid, nrc]),
                f"模型判定 0x{nrc:02X}，但 legacy 响应为 {response.hex(' ')}",
            )
        else:
            self.assertNotEqual(
                response[0],
                0x7F,
                f"模型判定通过，但 legacy 返回了否定响应 {response.hex(' ')}",
            )
        return response

    def test_frozen_nrc_anchors(self):
        """设计文档 §15.2 的冻结锚点（与 contract pins 同源，此处从**模型**侧验证）。"""
        anchors = [
            (SID_WRITE, bytes([0x2E, 0xFF, 0xFF]) + b"X",
             make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
             bytes([0x7F, 0x2E, 0x31])),
            (SID_WRITE, bytes([0x2E, 0xF1, 0x02]) + b"X",
             make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
             bytes([0x7F, 0x2E, 0x7F])),
            (SID_WRITE, bytes([0x2E, 0xF1, 0x90]) + b"X",
             make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LOCKED),
             bytes([0x7F, 0x2E, 0x33])),
            (SID_WRITE, bytes([0x2E, 0xF1, 0x90]) + b"X",
             make_state(uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1),
             bytes([0x7F, 0x2E, 0x22])),
            (SID_READ, bytes([0x22, 0xF1, 0x87, 0xFF, 0xFF]),
             make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
             bytes([0x7F, 0x22, 0x33])),
            (SID_READ, bytes([0x22, 0xFF, 0xFF, 0xF1, 0x87]),
             make_state(uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED),
             bytes([0x7F, 0x22, 0x31])),
        ]
        for sid, request, state, expected in anchors:
            with self.subTest(request=request.hex(" ")):
                response = self._assert_agreement(sid, request, state)
                self.assertEqual(response, expected)

    def test_0x2e_matrix_agreement(self):
        """``0x2E`` 的全组合对拍：DID × 会话 × 安全 × 数据长度。"""
        dids = (DID_WRITABLE, DID_READONLY, DID_UNKNOWN)
        payloads = (b"", b"X", b"A" * 16, b"A" * 17)

        for did, session, security, data in itertools.product(
            dids, SESSIONS, SECURITY_LEVELS, payloads
        ):
            request = bytes([SID_WRITE, (did >> 8) & 0xFF, did & 0xFF]) + data
            with self.subTest(
                did=hex(did), session=hex(session), security=security, n=len(data)
            ):
                self._assert_agreement(
                    SID_WRITE, request, make_state(session, security)
                )

    def test_0x22_matrix_agreement(self):
        """``0x22`` 的全组合对拍：单/多 DID（**含顺序**）× 会话 × 安全。"""
        single = (DID_READONLY, DID_PROTECTED, DID_UNKNOWN)
        lists = [bytes([SID_READ, (d >> 8) & 0xFF, d & 0xFF]) for d in single]
        lists += [
            bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF]),   # 受保护在前
            bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87]),   # 未知在前
            bytes([SID_READ, 0xF1, 0x90, 0xFF, 0xFF]),   # 公开 + 未知
        ]

        for request, session, security in itertools.product(
            lists, SESSIONS, SECURITY_LEVELS
        ):
            with self.subTest(
                request=request.hex(" "), session=hex(session), security=security
            ):
                self._assert_agreement(
                    SID_READ, request, make_state(session, security)
                )

    def test_agreement_holds_for_malformed_lengths(self):
        """畸形长度（0x22 的偶数长度规则、0x2E 的最短长度）也必须一致。"""
        malformed = [
            (SID_READ, bytes([SID_READ])),
            (SID_READ, bytes([SID_READ, 0xF1])),
            (SID_READ, bytes([SID_READ, 0xF1, 0x90, 0xF1])),
            (SID_WRITE, bytes([SID_WRITE])),
            (SID_WRITE, bytes([SID_WRITE, 0xF1])),
            (SID_WRITE, bytes([SID_WRITE, 0xF1, 0x02])),
        ]
        for sid, request in malformed:
            for session, security in itertools.product(SESSIONS, SECURITY_LEVELS):
                with self.subTest(request=request.hex(" "), session=hex(session)):
                    self._assert_agreement(
                        sid, request, make_state(session, security)
                    )


class TestLegacyExecutionPathUnchanged(unittest.TestCase):
    """
    Phase 1 的**核心约束**：legacy 执行路径仍然权威。

    这些断言与 contract pins 同源，但放在这里是为了让"Phase 1 未切换执行路径"
    这件事在本文件内**自证**（而不是只能靠外部文件）。
    """

    def test_static_registry_is_still_the_execution_source(self):
        self.assertEqual(len(SERVICES), 14)
        self.assertIn(SID_READ, SERVICES)
        self.assertIn(SID_WRITE, SERVICES)
        self.assertNotIn(SID_SECURITY_ACCESS, SERVICES)

    def test_services_without_authorization_still_ignore_session(self):
        """非授权服务在三种会话下响应必须完全相同（Phase 1 不得改变）。"""
        requests = [
            bytes([0x14, 0xFF, 0xFF, 0xFF]),
            bytes([0x19, 0x0A]),
            bytes([0x85, 0x01]),
        ]
        for request in requests:
            with self.subTest(request=request.hex(" ")):
                responses = set()
                for session in SESSIONS:
                    state = make_state(session, uds_types.SECURITY_LOCKED)
                    responses.add(handle_uds_request(request, state))
                self.assertEqual(
                    len(responses), 1, "非授权服务不得因会话不同而响应不同"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
