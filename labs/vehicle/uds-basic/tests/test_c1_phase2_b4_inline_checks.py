#!/usr/bin/env python3
"""
UDS Basic —— C1 Phase 2 Migration **B4**：inline defensive-check 审计

设计出处：``docs/OQ_C1_PHASE2_MIGRATION_DESIGN_REVIEW_2026-09-25.md`` §10.4；
A4 证据：``docs/OQ_C1_PHASE2_A4_INLINE_CHECKS_2026-09-25.md``。

## 本文件回答的唯一问题

> 当 dispatcher / ServiceSpec guard 被**绕过**、直接调用 handler 时，
> handler 自己是否仍然保持原有的 defensive authorization / capability checks？

即 §10.4 冻结的分工：

```
    Dispatcher → ServiceSpec guards → handler → handler inline checks

正常请求    ： guard → handler（两道都在）
直接调用    ： guard 被绕过  → **inline defense 仍必须阻止非法行为**（本文件证明）
```

## 两类证据缺一不可（设计 §10 of B4）

* **A. source-level**：断言 handler 源码段内**确实存在**各条检查；
* **B. behavior-level**：**直接调用** handler（完全绕过 dispatcher / resolver / guards），
  断言非法请求仍被拒绝、且**无不应有的副作用**。

只做 grep 的"静态存在"不是审计。本文件两者都做。

## 覆盖范围

* **0x22** —— A4 已审计；此处把该结论**固化为常驻测试**并复核（不重新设计）。
* **0x2E** —— 本阶段重点：Migration B 新迁移的服务，做完整独立审计。

运行：

    python3 tests/test_c1_phase2_b4_inline_checks.py
或：
    bash tests/run_tests.sh unit
"""

import ast
import os
import sys
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
sys.path.insert(0, _ECU_DIR)

import uds_server  # noqa: E402
import uds_types  # noqa: E402
from did_database import WRITABLE_DID_LENGTHS  # noqa: E402
from uds_server import (  # noqa: E402
    _SPEC_EXECUTION,
    SERVICE_SPECS,
    SERVICES,
    EcuState,
)

SID_READ = 0x22
SID_WRITE = 0x2E

DID_WRITABLE = 0xF102
DID_READONLY_VIN = 0xF190
DID_READONLY_BOOT = 0xF180
DID_READONLY_PROTECTED = 0xF187
DID_UNKNOWN = 0xFFFF

WRITABLE_MIN_LEN, WRITABLE_MAX_LEN = WRITABLE_DID_LENGTHS[DID_WRITABLE]

_SERVER_SRC = None


def server_source() -> str:
    """读取 ``uds_server.py`` 源码（带缓存）。"""
    global _SERVER_SRC
    if _SERVER_SRC is None:
        with open(os.path.join(_ECU_DIR, "uds_server.py"), encoding="utf-8") as handle:
            _SERVER_SRC = handle.read()
    return _SERVER_SRC


def handler_segment(name: str) -> str:
    """返回指定 ``FunctionDef`` 的**源码段**（AST 精确定位，含 docstring）。"""
    source = server_source()
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return ast.get_source_segment(source, node)


def make_state(session=uds_types.SESSION_EXTENDED, security=uds_types.SECURITY_LEVEL1):
    state = EcuState()
    state.session = session
    state.security_level = security
    return state


def direct_handler(sid: int, request: bytes, state: EcuState) -> bytes:
    """
    **直接调用** handler —— 完全绕过 dispatcher / ``resolve_service_spec`` / guards。

    这是本文件的核心入口：它刻意**不**经过 ``handle_uds_request``。
    """
    return SERVICES[sid](request, state)


class TestBypassMechanismIsReal(unittest.TestCase):
    """
    先证明"绕过"是真的：把 dispatcher 的解析与 guard 求值全部换成抛异常，
    直接调用仍须正常工作。

    否则后续"direct-handler 测试"可能只是又走了一遍 dispatcher。
    """

    def test_direct_call_does_not_touch_resolver_or_guards(self):
        boom = AssertionError("direct handler call 不得触碰 dispatcher 机制")

        with (
            patch.object(uds_server, "resolve_service_spec", side_effect=boom),
            patch.object(uds_server, "_guards_for", side_effect=boom),
            patch.object(uds_server, "handle_uds_request", side_effect=boom),
        ):
            response = direct_handler(
                SID_WRITE,
                bytes([SID_WRITE, 0xF1, 0x02]) + b"X",
                make_state(),
            )

        self.assertEqual(response, bytes([0x6E, 0xF1, 0x02]))

    def test_direct_call_returns_negative_for_illegal_request_under_patch(self):
        """绕过 dispatcher 后，非法请求仍由 handler 自己拒绝。"""
        boom = AssertionError("direct handler call 不得触碰 dispatcher 机制")

        with (
            patch.object(uds_server, "resolve_service_spec", side_effect=boom),
            patch.object(uds_server, "_guards_for", side_effect=boom),
        ):
            response = direct_handler(
                SID_WRITE,
                bytes([SID_WRITE, 0xFF, 0xFF]) + b"X",
                make_state(),
            )

        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x31]))

    def test_migration_context(self):
        """记录审计时点：两个服务都已迁移（inline check 不再是唯一防线，但仍须在）。"""
        self.assertEqual(_SPEC_EXECUTION, frozenset({SID_READ, SID_WRITE}))


class TestSourceEvidence0x22(unittest.TestCase):
    """A. source-level —— ``service_read_data_by_identifier()`` 的 inline checks 仍在。"""

    @classmethod
    def setUpClass(cls):
        cls.body = handler_segment("service_read_data_by_identifier")

    def _assert(self, label, pattern):
        self.assertIn(pattern, self.body, f"0x22 inline check 缺失：{label}")

    def test_request_length_check(self):
        self._assert("length", "len(request) < 3")
        self._assert("odd DID list", "(len(request) - 1) % 2")

    def test_ordered_did_loop(self):
        self._assert("ordered loop", "for offset in range(1, len(request), 2):")

    def test_did_parsing(self):
        self._assert("did parse", "did = (request[offset] << 8) | request[offset + 1]")

    def test_did_existence_lookup(self):
        self._assert("override precedence", "did in state.did_overrides")
        self._assert("factory database", "DID_DATABASE.get(did)")

    def test_unknown_did_nrc(self):
        self._assert("data is None", "if data is None:")
        self._assert("0x31", "NRC_REQUEST_OUT_OF_RANGE")

    def test_security_level_check(self):
        self._assert(
            "security", "if state.security_level < required_security_level(did):"
        )
        self._assert("0x33", "NRC_SECURITY_ACCESS_DENIED")

    def test_all_or_nothing_structure(self):
        self._assert("independent buffer", "payload = bytearray()")
        self._assert(
            "single positive return",
            "return uds_types.positive_response(0x22, bytes(payload))",
        )

    def test_handler_has_no_assumption_that_guards_already_ran(self):
        """handler 不得出现"guard 已检查过"之类的假设性依赖。"""
        for token in ("_guards_for", "_guard_read_did", "resolve_service_spec"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.body)


class TestSourceEvidence0x2E(unittest.TestCase):
    """A. source-level —— ``service_write_data_by_identifier()`` 的六道 inline rung 仍在。"""

    @classmethod
    def setUpClass(cls):
        cls.body = handler_segment("service_write_data_by_identifier")

    def _assert(self, label, pattern):
        self.assertIn(pattern, self.body, f"0x2E inline check 缺失：{label}")

    def test_rung1_length_check(self):
        self._assert("rung1 length", "len(request) < 4")
        self._assert("rung1 nrc", "NRC_INCORRECT_MESSAGE_LENGTH")

    def test_rung2_did_existence(self):
        self._assert("rung2", "if did not in DID_DATABASE:")
        self._assert("rung2 nrc", "NRC_REQUEST_OUT_OF_RANGE")

    def test_rung3_session_check_keeps_frozen_writing(self):
        """OD-5 冻结：必须仍是 ``== SESSION_DEFAULT``，不得改写成 ``not in {...}``。"""
        self._assert("rung3", "if state.session == uds_types.SESSION_DEFAULT:")
        self._assert("rung3 nrc", "NRC_SERVICE_NOT_SUPPORTED_IN_SESSION")

    def test_rung4_security_check(self):
        self._assert(
            "rung4", "if state.security_level < uds_types.SECURITY_LEVEL1:"
        )
        self._assert("rung4 nrc", "NRC_SECURITY_ACCESS_DENIED")

    def test_rung5_writability_check(self):
        self._assert("rung5", "if did not in WRITABLE_DIDS:")
        self._assert("rung5 nrc", "NRC_CONDITIONS_NOT_CORRECT")

    def test_rung6_data_length_check(self):
        self._assert("rung6 bounds", "WRITABLE_DID_LENGTHS[did]")
        self._assert("rung6 range", "if not min_len <= len(data) <= max_len:")

    def test_handler_has_no_assumption_that_guards_already_ran(self):
        for token in ("_guards_for", "_guard_write_did", "resolve_service_spec"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.body)


class Test0x2EDirectHandlerBypass(unittest.TestCase):
    """
    B. behavior-level —— **直接调用** 0x2E handler，各 capability / authorization
    非法状态仍被 handler 自己拒绝，且**无不应有的副作用**。
    """

    def _call(self, request, session=uds_types.SESSION_EXTENDED,
              security=uds_types.SECURITY_LEVEL1):
        state = make_state(session, security)
        return direct_handler(SID_WRITE, request, state), state

    # ---- Case A：DEFAULT 会话 ----

    def test_case_a_default_session(self):
        response, state = self._call(
            bytes([SID_WRITE, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF]) + b"X",
            session=uds_types.SESSION_DEFAULT,
            security=uds_types.SECURITY_LEVEL1,
        )
        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x7F]))
        self.assertEqual(state.did_overrides, {})

    # ---- Case B：security 不足 ----

    def test_case_b_insufficient_security(self):
        response, state = self._call(
            bytes([SID_WRITE, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF]) + b"X",
            security=uds_types.SECURITY_LOCKED,
        )
        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x33]))
        self.assertEqual(state.did_overrides, {})

    # ---- Case C：只读 DID ----

    def test_case_c_readonly_dids(self):
        for did in (DID_READONLY_VIN, DID_READONLY_BOOT, DID_READONLY_PROTECTED):
            with self.subTest(did=hex(did)):
                response, state = self._call(
                    bytes([SID_WRITE, (did >> 8) & 0xFF, did & 0xFF]) + b"X"
                )
                self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x22]))
                self.assertEqual(
                    state.did_overrides, {}, "被拒绝的写入不得产生 did_overrides 副作用"
                )

    # ---- Case D：未知 DID ----

    def test_case_d_unknown_did(self):
        response, state = self._call(
            bytes([SID_WRITE, 0xFF, 0xFF]) + b"X",
            session=uds_types.SESSION_DEFAULT,
            security=uds_types.SECURITY_LOCKED,
        )
        # 即使会话与安全都不满足，未知 DID 仍先被 handler 自己判出（inline rung 顺序）
        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x31]))
        self.assertEqual(state.did_overrides, {})

    # ---- Case E：畸形长度 ----

    def test_case_e_malformed_length(self):
        for request in (
            bytes([SID_WRITE]),
            bytes([SID_WRITE, 0xF1]),
            bytes([SID_WRITE, 0xF1, 0x02]),
        ):
            with self.subTest(request=request.hex(" ")):
                response, state = self._call(request)
                self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x13]))
                self.assertEqual(state.did_overrides, {})

    # ---- Case F：数据长度越界 ----

    def test_case_f_data_length_zero_is_caught_by_length_rung(self):
        """``2E F1 02``（数据为空）在**线上**长度只有 3，由 inline rung① 拦下。"""
        response, state = self._call(bytes([SID_WRITE, 0xF1, 0x02]))
        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x13]))
        self.assertEqual(state.did_overrides, {})

    def test_case_f_data_length_over_max(self):
        """超出闭区间上界（16）由 inline rung⑥ 拦下。"""
        for length in (WRITABLE_MAX_LEN + 1, WRITABLE_MAX_LEN + 8):
            with self.subTest(length=length):
                response, state = self._call(
                    bytes([SID_WRITE, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF])
                    + b"X" * length
                )
                self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x13]))
                self.assertEqual(state.did_overrides, {})

    # ---- Case G：合法写入仍可用 ----

    def test_case_g_valid_write_still_succeeds(self):
        """直接调用下合法写入必须**正常工作** —— inline check 不是"一律拒绝"。"""
        response, state = self._call(
            bytes([SID_WRITE, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF]) + b"OK"
        )
        self.assertEqual(response, bytes([0x6E, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF]))
        self.assertEqual(state.did_overrides[DID_WRITABLE], b"OK")

    def test_valid_write_at_both_length_bounds(self):
        """闭区间端点（1 与 16）都应为合法写入。"""
        for length in (WRITABLE_MIN_LEN, WRITABLE_MAX_LEN):
            with self.subTest(length=length):
                payload = bytes([0x41] * length)
                response, state = self._call(
                    bytes([SID_WRITE, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF]) + payload
                )
                self.assertEqual(
                    response, bytes([0x6E, DID_WRITABLE >> 8, DID_WRITABLE & 0xFF])
                )
                self.assertEqual(state.did_overrides[DID_WRITABLE], payload)

    def test_override_does_not_make_readonly_did_writable_inline(self):
        """``did_overrides`` 预置**不**改变 inline 的 writability 判定。"""
        state = make_state()
        state.did_overrides[DID_READONLY_VIN] = b"GHOST"
        response = direct_handler(
            SID_WRITE, bytes([SID_WRITE, 0xF1, 0x90]) + b"X", state
        )
        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x22]))

    def test_override_does_not_make_unknown_did_writable_inline(self):
        """未知 DID 的 override 也**不**使其可写（inline rung② 只看 `DID_DATABASE`）。"""
        state = make_state()
        state.did_overrides[DID_UNKNOWN] = b"GHOST"
        response = direct_handler(
            SID_WRITE, bytes([SID_WRITE, 0xFF, 0xFF]) + b"X", state
        )
        self.assertEqual(response, bytes([0x7F, SID_WRITE, 0x31]))

    # ---- inline rung 顺序（direct 调用下同样成立）----

    def test_inline_rung_order_preserved_on_direct_call(self):
        cases = [
            (bytes([SID_WRITE]), uds_types.SESSION_DEFAULT, uds_types.SECURITY_LOCKED, 0x13),
            (bytes([SID_WRITE, 0xFF, 0xFF]) + b"X", uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x31),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X", uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x7F),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X", uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LOCKED, 0x33),
            (bytes([SID_WRITE, 0xF1, 0x90]) + b"X", uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LEVEL1, 0x22),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X" * 17, uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LEVEL1, 0x13),
        ]
        for request, session, security, expected in cases:
            with self.subTest(nrc=hex(expected)):
                response, _ = self._call(request, session, security)
                self.assertEqual(response, bytes([0x7F, SID_WRITE, expected]))


class Test0x22DirectHandlerBypass(unittest.TestCase):
    """
    B. behavior-level —— **直接调用** 0x22 handler 的最小 smoke。

    A4 已完成 0x22 的独立审计；此处把该结论**固化为常驻测试**，
    并复核当前源码与 A4 audited 源码一致（A4 报告记录 production diff = 0）。
    """

    def _call(self, request, session=uds_types.SESSION_EXTENDED,
              security=uds_types.SECURITY_LOCKED):
        return direct_handler(SID_READ, request, make_state(session, security))

    def test_malformed_length(self):
        for request in (bytes([SID_READ]), bytes([SID_READ, 0xF1]),
                        bytes([SID_READ, 0xF1, 0x90, 0xF1])):
            with self.subTest(request=request.hex(" ")):
                self.assertEqual(
                    self._call(request), bytes([0x7F, SID_READ, 0x13])
                )

    def test_unknown_did(self):
        self.assertEqual(
            self._call(bytes([SID_READ, 0xFF, 0xFF])),
            bytes([0x7F, SID_READ, 0x31]),
        )

    def test_protected_did_blocked_while_locked(self):
        self.assertEqual(
            self._call(bytes([SID_READ, 0xF1, 0x87])),
            bytes([0x7F, SID_READ, 0x33]),
        )

    def test_public_did_readable(self):
        response = self._call(bytes([SID_READ, 0xF1, 0x90]))
        self.assertEqual(response[0], 0x62)

    def test_protected_did_readable_when_unlocked(self):
        response = self._call(
            bytes([SID_READ, 0xF1, 0x87]), security=uds_types.SECURITY_LEVEL1
        )
        self.assertEqual(response[0], 0x62)

    def test_frozen_anchors_on_direct_call(self):
        """两个顺序锚点在 direct 调用下同样成立（inline 短路，非 guard）。"""
        self.assertEqual(
            self._call(bytes([SID_READ, 0xF1, 0x87, 0xFF, 0xFF])),
            bytes([0x7F, SID_READ, 0x33]),
        )
        self.assertEqual(
            self._call(bytes([SID_READ, 0xFF, 0xFF, 0xF1, 0x87])),
            bytes([0x7F, SID_READ, 0x31]),
        )

    def test_read_has_no_side_effect(self):
        state = make_state(
            session=uds_types.SESSION_EXTENDED, security=uds_types.SECURITY_LOCKED
        )
        direct_handler(SID_READ, bytes([SID_READ, 0xF1, 0x90]), state)
        self.assertEqual(state.did_overrides, {})
        self.assertEqual(state.session, uds_types.SESSION_EXTENDED)
        self.assertEqual(state.security_level, uds_types.SECURITY_LOCKED)


class TestInlineDefenseIsIndependentOfSpecModel(unittest.TestCase):
    """
    §11 —— 证明 ``ServiceSpec`` guards **不是** handler 的唯一安全边界。

    在不引用 ``SERVICE_SPECS`` / ``_guards_for`` 的前提下，
    仅凭 handler 自身即可拒绝全部非法请求。
    """

    def test_all_illegal_2e_requests_rejected_without_any_spec_reference(self):
        cases = [
            (bytes([SID_WRITE]), uds_types.SESSION_EXTENDED, uds_types.SECURITY_LEVEL1, 0x13),
            (bytes([SID_WRITE, 0xFF, 0xFF]) + b"X", uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x31),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X", uds_types.SESSION_DEFAULT,
             uds_types.SECURITY_LOCKED, 0x7F),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X", uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LOCKED, 0x33),
            (bytes([SID_WRITE, 0xF1, 0x90]) + b"X", uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LEVEL1, 0x22),
            (bytes([SID_WRITE, 0xF1, 0x02]) + b"X" * 17, uds_types.SESSION_EXTENDED,
             uds_types.SECURITY_LEVEL1, 0x13),
        ]
        for request, session, security, expected in cases:
            with self.subTest(nrc=hex(expected)):
                response = direct_handler(
                    SID_WRITE, request, make_state(session, security)
                )
                self.assertEqual(response, bytes([0x7F, SID_WRITE, expected]))
                self.assertNotEqual(response[0], 0x6E)

    def test_handler_identity_is_the_same_object_as_in_the_registry(self):
        """inline 防线就是生产注册表里的那个 handler，不是测试替身。"""
        self.assertIs(SERVICES[SID_READ], SERVICE_SPECS[SID_READ].handler)
        self.assertIs(SERVICES[SID_WRITE], SERVICE_SPECS[SID_WRITE].handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)
