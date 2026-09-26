#!/usr/bin/env python3
"""
C3-A1 —— Gateway 路径的 0x27 SecurityAccess 契约

设计出处：``docs/OQ_C3_SECURITY_ACCESS_GATEWAY_DESIGN_REVIEW_2026-09-26.md``
（C3-OD-1 … OD-15、§6 行为契约、§9 选定设计、§10 functional、§11 隔离、§12 override）。

## 本阶段证明什么

> Gateway 路径**能够执行既有的** 0x27 状态机 —— 且**只有一份**状态机。

```
入口层注入依赖         uds_server.security_access_services(now, security_config)
      ↓                （与直连 ECU 路径**同一份定义**）
VirtualEcu 默认 handler 每请求 {**SERVICES, **provider()}
      ↓
uds_server.handle_uds_request
      ↓
uds_server.service_security_access   ← **唯一**的 0x27 状态机（未复制）
```

## 本阶段**不**证明

```
不负责  Direct ECU ↔ Gateway 差分终判        → C3-A2
不负责  provider bypass 的完整审计            → C3-A4
不负责  full regression closure               → C3-A3

因此本文件**不**声称两路径等价 —— 那是 A2 的职责。
```

## 已知限制（设计 §10.3 冻结，本阶段**如实保留**）

```
functional 0x27 = ALLOW（C3 未添加任何专门机制）
    · 网关恒不收集响应（C2-OD-4）
    · 每个 ECU 的 pending_seed / seed_counter 会被推进（空转副作用）
    · 因 keygen 只取决于 (counter, level)，各 ECU 该次 seed 取值相同
    · 不影响 security_level、不消耗 failed_attempts
```

运行：

    python3 tests/test_c3_a1_gateway_security_access.py
或：
    bash tests/run_tests.sh unit
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
if _ECU_DIR not in sys.path:
    sys.path.insert(0, _ECU_DIR)

import keygen  # noqa: E402
import uds_server  # noqa: E402
import uds_types  # noqa: E402
from gateway import (  # noqa: E402
    FUNCTIONAL_ADDRESS,
    DiagnosticGateway,
    VirtualEcu,
    build_default_gateway,
)
from uds_server import EcuState  # noqa: E402

SID_SECURITY_ACCESS = 0x27
SID_READ_DID = 0x22
SID_SESSION = 0x10
SID_ECU_RESET = 0x11

REQUEST_SEED = bytes([SID_SECURITY_ACCESS, 0x01])
DID_PROTECTED = 0xF187
DID_PUBLIC = 0xF190

ECU_A_REQ, ECU_A_RESP = 0x7E0, 0x7E8
ECU_B_REQ, ECU_B_RESP = 0x7E1, 0x7E9
ECU_C_REQ, ECU_C_RESP = 0x7E2, 0x7EA

NRC_INVALID_KEY = 0x35
NRC_EXCEED_ATTEMPTS = 0x36
NRC_TIME_DELAY = 0x37
NRC_SEQUENCE = 0x24
NRC_CONDITIONS = 0x22
NRC_SECURITY_DENIED = 0x33
NRC_INCORRECT_LENGTH = 0x13

#: 测试用固定时钟与安全配置（**测试侧**依赖，不是生产常量）。
BASE_CLOCK = 1000.0
TEST_SECURITY_CONFIG = {
    "max_attempts": 3,
    "lockout_seconds": 10,
    "seed_expiry_seconds": 5,
}


class Clock:
    """可推进的确定性时钟（测试侧依赖）。"""

    def __init__(self, now: float = BASE_CLOCK):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_provider(clock: Clock, config: dict = TEST_SECURITY_CONFIG):
    """
    **入口层注入**的等价物（测试侧）：每请求调用一次
    ``uds_server.security_access_services(...)``。

    刻意复用**同一个**唯一绑定定义 —— 与 ``gateway_runtime`` 的入口层接线同构。
    """

    def provider():
        return uds_server.security_access_services(
            now=clock(), security_config=config
        )

    return provider


def make_ecu(name, request_id, response_id, *, session=None, security=None):
    state = EcuState()
    state.session = uds_types.SESSION_DEFAULT if session is None else session
    state.security_level = uds_types.SECURITY_LOCKED if security is None else security
    return VirtualEcu(name, request_id, response_id, state=state)


def wired_gateway(clock=None, config=None, ecus=None, policy=None):
    """接线 0x27 的默认三 ECU 网关（测试装置）。"""
    clock = clock or Clock()
    eh = make_ecu
    ecus = ecus if ecus is not None else [
        eh("ECU_A", ECU_A_REQ, ECU_A_RESP),
        eh("ECU_B", ECU_B_REQ, ECU_B_RESP),
        eh("ECU_C", ECU_C_REQ, ECU_C_RESP),
    ]
    return DiagnosticGateway(
        ecus,
        policy=policy,
        services_provider=make_provider(clock, config or TEST_SECURITY_CONFIG),
    )


def read_source(module) -> str:
    """读取模块源码（用于结构性不变量断言）—— 显式关闭文件。"""
    with open(module.__file__, encoding="utf-8") as handle:
        return handle.read()


def parse_seed(response: bytes) -> bytes:
    assert response[0] == 0x67 and response[1] == 0x01, response.hex(" ")
    return response[2:]


def is_nrc(response: bytes, nrc: int) -> bool:
    return response[:3] == bytes([0x7F, SID_SECURITY_ACCESS, nrc])


# ======================================================================
# 1 / 11 —— physical 27 01 与响应 ID 映射
# ======================================================================


class TestPhysicalRequestSeed(unittest.TestCase):
    """C3-A1 覆盖项 1 与 11。"""

    def test_gateway_yields_a_seed_for_the_target_ecu(self):
        gw = wired_gateway()
        results = gw.route(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(len(results), 1)
        response_id, response = results[0]
        self.assertEqual(response_id, ECU_A_RESP, "响应必须落在目标 ECU 的 response_id")
        self.assertEqual(response[0], 0x67)
        self.assertEqual(response[1], 0x01)
        self.assertEqual(len(response), 2 + keygen.SEED_LENGTH)

    def test_response_id_mapping_for_every_ecu(self):
        """覆盖项 11：A→0x7E8 / B→0x7E9 / C→0x7EA。"""
        expected = {
            ECU_A_REQ: ECU_A_RESP,
            ECU_B_REQ: ECU_B_RESP,
            ECU_C_REQ: ECU_C_RESP,
        }
        for request_id, response_id in expected.items():
            with self.subTest(request_id=hex(request_id)):
                gw = wired_gateway()
                results = gw.route(request_id, REQUEST_SEED)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0][0], response_id)

    def test_seed_is_not_all_zero_and_changes_between_requests(self):
        gw = wired_gateway()
        first = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
        second = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])

        self.assertNotEqual(first, bytes(keygen.SEED_LENGTH))
        self.assertNotEqual(first, second, "seed_counter 递增 ⇒ 两次 seed 不同")

    def test_unwired_gateway_does_not_provide_security_access(self):
        """
        C3-OD-7：机制与接线分离 —— ``build_default_gateway()`` 默认**不**接线，
        因此其行为与 C3 引入前**逐字节相同**。
        """
        gw = build_default_gateway()
        self.assertEqual(
            gw.route(ECU_A_REQ, REQUEST_SEED),
            [(ECU_A_RESP, bytes([0x7F, SID_SECURITY_ACCESS, 0x11]))],
        )


# ======================================================================
# 2 —— 27 02 复用既有状态机
# ======================================================================


class TestSendKeyReusesExistingStateMachine(unittest.TestCase):
    """C3-A1 覆盖项 2。"""

    def test_correct_key_unlocks_the_target_ecu(self):
        gw = wired_gateway()
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])

        results = gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))

        self.assertEqual(results, [(ECU_A_RESP, bytes([0x67, 0x02]))])
        self.assertEqual(gw.ecu_by_name("ECU_A").state.security_level, uds_types.SECURITY_LEVEL1)

    def test_the_state_machine_is_the_protocol_layers_single_definition(self):
        """C3-OD-1：网关路径**调用**既有状态机，而不是自带一份。"""
        import gateway as gateway_module

        source = read_source(gateway_module)
        for forbidden in ("seed_counter", "failed_attempts", "lockout_until", "verify_key"):
            with self.subTest(token=forbidden):
                self.assertNotIn(
                    forbidden, source, "网关层不得出现 0x27 状态机的实现细节"
                )

    def test_gateway_runtime_wiring_uses_the_single_definition(self):
        """入口层（gateway_runtime）委托同一个唯一绑定定义。"""
        import gateway_runtime

        source = read_source(gateway_runtime)
        self.assertIn("security_access_services", source)
        self.assertIn("ecu._now()", source)
        self.assertIn("ecu.SECURITY_CONFIG", source)


# ======================================================================
# 3 / 4 —— 错误 key 与 lockout
# ======================================================================


class TestWrongKeyAndLockout(unittest.TestCase):
    """C3-A1 覆盖项 3 与 4。"""

    def _wrong_key_for(self, seed: bytes) -> bytes:
        correct = keygen.derive_key(seed)
        return bytes(b ^ 0xFF for b in correct)

    def test_wrong_key_returns_35_and_increments_failed_attempts(self):
        gw = wired_gateway()
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
        payload = bytes([SID_SECURITY_ACCESS, 0x02]) + self._wrong_key_for(seed)

        results = gw.route(ECU_A_REQ, payload)

        self.assertTrue(is_nrc(results[0][1], NRC_INVALID_KEY), results[0][1].hex(" "))
        self.assertEqual(gw.ecu_by_name("ECU_A").state.failed_attempts, 1)

    def test_third_failure_enters_lockout_and_then_returns_37(self):
        gw = wired_gateway()
        for expected_nrc in (NRC_INVALID_KEY, NRC_INVALID_KEY, NRC_EXCEED_ATTEMPTS):
            seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
            payload = bytes([SID_SECURITY_ACCESS, 0x02]) + self._wrong_key_for(seed)
            results = gw.route(ECU_A_REQ, payload)
            with self.subTest(expected=hex(expected_nrc)):
                self.assertTrue(is_nrc(results[0][1], expected_nrc), results[0][1].hex(" "))

        locked = gw.route(ECU_A_REQ, REQUEST_SEED)
        self.assertTrue(is_nrc(locked[0][1], NRC_TIME_DELAY), locked[0][1].hex(" "))

    def test_lockout_only_affects_the_target_ecu(self):
        """覆盖项 4：锁定不得波及其它 ECU。"""
        gw = wired_gateway()
        for _ in range(3):
            seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
            gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + self._wrong_key_for(seed))

        self.assertTrue(is_nrc(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1], NRC_TIME_DELAY))
        for request_id, response_id in ((ECU_B_REQ, ECU_B_RESP), (ECU_C_REQ, ECU_C_RESP)):
            with self.subTest(request_id=hex(request_id)):
                results = gw.route(request_id, REQUEST_SEED)
                self.assertEqual(results[0][0], response_id)
                self.assertEqual(results[0][1][0], 0x67, "其它 ECU 不受 A 的锁定影响")

    def test_lockout_expires_with_the_injected_clock(self):
        clock = Clock()
        gw = wired_gateway(clock=clock)
        for _ in range(3):
            seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
            gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + self._wrong_key_for(seed))

        self.assertTrue(is_nrc(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1], NRC_TIME_DELAY))

        clock.advance(TEST_SECURITY_CONFIG["lockout_seconds"] + 1)
        after = gw.route(ECU_A_REQ, REQUEST_SEED)
        self.assertEqual(after[0][1][0], 0x67, "锁定到期后应恢复")
        self.assertEqual(gw.ecu_by_name("ECU_A").state.failed_attempts, 0)


# ======================================================================
# 5 / 6 / 7 —— seed 过期 / 重放 / 已解锁再请求
# ======================================================================


class TestSeedLifecycle(unittest.TestCase):
    """C3-A1 覆盖项 5、6、7。"""

    def test_seed_expiry_returns_24(self):
        clock = Clock()
        gw = wired_gateway(clock=clock)
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])

        clock.advance(TEST_SECURITY_CONFIG["seed_expiry_seconds"] + 1)
        results = gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))

        self.assertTrue(is_nrc(results[0][1], NRC_SEQUENCE), results[0][1].hex(" "))

    def test_consumed_seed_replay_returns_24(self):
        gw = wired_gateway()
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
        key = keygen.derive_key(seed)

        self.assertEqual(gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + key)[0][1], bytes([0x67, 0x02]))
        replay = gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + key)
        self.assertTrue(is_nrc(replay[0][1], NRC_SEQUENCE), replay[0][1].hex(" "))

    def test_request_seed_while_unlocked_returns_22(self):
        gw = wired_gateway()
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
        gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))

        results = gw.route(ECU_A_REQ, REQUEST_SEED)
        self.assertTrue(is_nrc(results[0][1], NRC_CONDITIONS), results[0][1].hex(" "))

    def test_malformed_lengths_return_13(self):
        gw = wired_gateway()
        for payload in (
            bytes([SID_SECURITY_ACCESS, 0x01, 0x00]),
            bytes([SID_SECURITY_ACCESS, 0x02, 0x00]),
        ):
            with self.subTest(payload=payload.hex(" ")):
                results = gw.route(ECU_A_REQ, payload)
                self.assertTrue(is_nrc(results[0][1], NRC_INCORRECT_LENGTH))

    def test_now_is_reevaluated_per_request(self):
        """
        C3-OD-14 限制 7/8：``now`` 必须**每请求**重新求值、不得缓存。

        证据：同一个 provider 下，先请求 seed，再把时钟推过有效期 ——
        若 now 被缓存，第二次的 SendKey 仍会成功；实际上必须返回 24。
        """
        clock = Clock()
        gw = wired_gateway(clock=clock)
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])

        clock.advance(TEST_SECURITY_CONFIG["seed_expiry_seconds"] + 1)
        results = gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))
        self.assertTrue(is_nrc(results[0][1], NRC_SEQUENCE), "陈旧 now 会掩盖 seed 过期")


# ======================================================================
# 8 / 9 —— 会话切换与 ECU 复位重锁
# ======================================================================


class TestSessionAndResetRelock(unittest.TestCase):
    """C3-A1 覆盖项 8 与 9 —— 完全复用既有 ``EcuState`` 语义。"""

    def _unlock(self, gw, request_id):
        seed = parse_seed(gw.route(request_id, REQUEST_SEED)[0][1])
        gw.route(request_id, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))

    def test_session_switch_relocks_the_target_ecu(self):
        gw = wired_gateway()
        self._unlock(gw, ECU_A_REQ)
        self.assertEqual(gw.ecu_by_name("ECU_A").state.security_level, uds_types.SECURITY_LEVEL1)

        gw.route(ECU_A_REQ, bytes([SID_SESSION, uds_types.SESSION_EXTENDED]))

        self.assertEqual(gw.ecu_by_name("ECU_A").state.security_level, uds_types.SECURITY_LOCKED)

    def test_ecu_reset_relocks_and_keeps_penalty_state(self):
        gw = wired_gateway()
        self._unlock(gw, ECU_A_REQ)
        state = gw.ecu_by_name("ECU_A").state
        state.failed_attempts = 1  # 惩罚状态：复位**不得**清除

        gw.route(ECU_A_REQ, bytes([SID_ECU_RESET, 0x01]))

        self.assertEqual(state.security_level, uds_types.SECURITY_LOCKED)
        self.assertEqual(state.failed_attempts, 1, "复位不得刷新暴力破解惩罚状态")

    def test_session_switch_does_not_affect_other_ecus(self):
        gw = wired_gateway()
        self._unlock(gw, ECU_B_REQ)
        gw.route(ECU_A_REQ, bytes([SID_SESSION, uds_types.SESSION_EXTENDED]))

        self.assertEqual(gw.ecu_by_name("ECU_B").state.security_level, uds_types.SECURITY_LEVEL1)


# ======================================================================
# 10 —— 受保护 DID
# ======================================================================


class TestProtectedDidThroughGateway(unittest.TestCase):
    """C3-A1 覆盖项 10。"""

    def test_locked_read_returns_33(self):
        gw = wired_gateway()
        results = gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x87]))
        self.assertEqual(results[0][1][:3], bytes([0x7F, SID_READ_DID, NRC_SECURITY_DENIED]))

    def test_unlocked_read_succeeds_after_gateway_unlock(self):
        gw = wired_gateway()
        gw.route(ECU_A_REQ, bytes([SID_SESSION, uds_types.SESSION_EXTENDED]))
        seed = parse_seed(gw.route(ECU_A_REQ, REQUEST_SEED)[0][1])
        gw.route(ECU_A_REQ, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))

        results = gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x87]))

        self.assertEqual(results[0][0], ECU_A_RESP)
        self.assertEqual(results[0][1][:3], bytes([0x62, 0xF1, 0x87]))

    def test_public_did_readable_while_locked(self):
        gw = wired_gateway()
        results = gw.route(ECU_B_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))
        self.assertEqual(results[0][0], ECU_B_RESP)
        self.assertEqual(results[0][1][:3], bytes([0x62, 0xF1, 0x90]))


# ======================================================================
# 12 / 13 —— C2 policy 与 0x27 的交互
# ======================================================================


class TestPolicyInteraction(unittest.TestCase):
    """C3-A1 覆盖项 12 与 13。"""

    def _deny_target(self, request_id):
        def policy(ecu, addressing_mode, sid):
            return ecu.request_id != request_id

        return policy

    def test_policy_allow_lets_the_gateway_reach_the_target_0x27(self):
        """覆盖项 13。"""
        gw = wired_gateway(policy=lambda ecu, mode, sid: True)
        results = gw.route(ECU_A_REQ, REQUEST_SEED)
        self.assertEqual(results[0][1][0], 0x67)

    def test_policy_deny_is_a_silent_drop_for_0x27(self):
        """覆盖项 12：deny ⇒ 无帧、不合成 NRC、目标 ECU 完全不可见。"""
        gw = wired_gateway(policy=self._deny_target(ECU_A_REQ))
        results, trace = gw.route_with_trace(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(results, [], "deny ⇒ 静默丢弃")
        self.assertEqual(len(trace.decisions), 1)
        self.assertFalse(trace.decisions[0].allowed)
        self.assertEqual(trace.decisions[0].sid, SID_SECURITY_ACCESS)

    def test_policy_deny_does_not_reach_the_target_ecu(self):
        calls = []
        ecu = make_ecu("ECU_A", ECU_A_REQ, ECU_A_RESP)
        original = ecu.process

        def spy(payload):
            calls.append(payload)
            return original(payload)

        ecu.process = spy
        gw = DiagnosticGateway([ecu], policy=self._deny_target(ECU_A_REQ))
        gw.route(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(calls, [], "deny ⇒ target.process() 不得被调用")
        self.assertIsNone(ecu.state.pending_seed, "deny ⇒ 0x27 状态不得被推进")

    def test_policy_dimension_is_still_the_frozen_triple(self):
        """C3-OD-5：C3 **未**给 policy 增加 0x27 特殊字段。"""
        seen = []

        def policy(ecu, addressing_mode, sid):
            seen.append((ecu.request_id, addressing_mode, sid))
            return True

        gw = wired_gateway(policy=policy)
        gw.route(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], (ECU_A_REQ, "physical", SID_SECURITY_ACCESS))

    def test_provider_does_not_change_policy_evaluation_count(self):
        """C3-OD-14 限制 3/4：provider 不得改变策略的求值次数与顺序。"""
        counts = []

        def policy(ecu, addressing_mode, sid):
            counts.append((ecu.request_id, addressing_mode))
            return True

        gw = wired_gateway(policy=policy)
        gw.route(ECU_A_REQ, REQUEST_SEED)
        self.assertEqual(counts, [(ECU_A_REQ, "physical")])

        counts.clear()
        gw.route(FUNCTIONAL_ADDRESS, REQUEST_SEED)
        self.assertEqual(counts, [(ECU_REQ, "functional") for ECU_REQ in (ECU_A_REQ, ECU_B_REQ, ECU_C_REQ)])


# ======================================================================
# 14 —— 多 ECU 隔离（为 C3-A2 提供实现基础）
# ======================================================================


class TestMultiEcuIsolation(unittest.TestCase):
    """C3-A1 覆盖项 14 —— 设计 §11 INV-1…5 的**实现基础**（终判属 C3-A2）。"""

    def _unlock(self, gw, request_id):
        seed = parse_seed(gw.route(request_id, REQUEST_SEED)[0][1])
        gw.route(request_id, bytes([SID_SECURITY_ACCESS, 0x02]) + keygen.derive_key(seed))

    def test_unlocking_a_does_not_unlock_b_or_c(self):
        gw = wired_gateway()
        self._unlock(gw, ECU_A_REQ)

        self.assertEqual(gw.ecu_by_name("ECU_A").state.security_level, uds_types.SECURITY_LEVEL1)
        for name in ("ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                self.assertEqual(gw.ecu_by_name(name).state.security_level, uds_types.SECURITY_LOCKED)

    def test_security_state_objects_are_distinct_per_ecu(self):
        gw = wired_gateway()
        states = [ecu.state for ecu in gw.ecus]
        self.assertEqual(len({id(s) for s in states}), len(states))

    def test_seed_counters_are_independent(self):
        gw = wired_gateway()
        gw.route(ECU_A_REQ, REQUEST_SEED)
        gw.route(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(gw.ecu_by_name("ECU_A").state.seed_counter, 2)
        self.assertEqual(gw.ecu_by_name("ECU_B").state.seed_counter, 0)
        self.assertEqual(gw.ecu_by_name("ECU_C").state.seed_counter, 0)

    def test_pending_seed_is_per_ecu(self):
        gw = wired_gateway()
        gw.route(ECU_B_REQ, REQUEST_SEED)

        self.assertIsNotNone(gw.ecu_by_name("ECU_B").state.pending_seed)
        self.assertIsNone(gw.ecu_by_name("ECU_A").state.pending_seed)
        self.assertIsNone(gw.ecu_by_name("ECU_C").state.pending_seed)

    def test_each_ecu_binds_its_own_state(self):
        """INV-5：每个 ECU 的 handler 必须绑定**它自己的** EcuState。"""
        gw = wired_gateway()
        gw.route(ECU_A_REQ, REQUEST_SEED)
        gw.route(ECU_B_REQ, REQUEST_SEED)
        gw.route(ECU_C_REQ, REQUEST_SEED)

        self.assertEqual([ecu.state.seed_counter for ecu in gw.ecus], [1, 1, 1])


# ======================================================================
# functional 0x27 —— 冻结的 known limitation（设计 §10.3）
# ======================================================================


class TestFunctionalSecurityAccessIsTheDocumentedLimitation(unittest.TestCase):
    """
    functional 0x27 = **ALLOW**（C3-OD-8），**无任何专门机制**。

    本类**如实锁定**设计 §10.3 记录的 known limitation，
    使"空转副作用"成为一个**已知、被断言过**的事实，而不是意外。
    """

    def test_functional_seed_returns_no_response(self):
        """C2-OD-4 不变：功能寻址恒不收集响应。"""
        gw = wired_gateway()
        self.assertEqual(gw.route(FUNCTIONAL_ADDRESS, REQUEST_SEED), [])

    def test_functional_seed_advances_every_ecus_seed_state(self):
        """已知副作用：三个 ECU 的 seed 状态**都被推进**，Tester 却拿不到 seed。"""
        gw = wired_gateway()
        gw.route(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        for name in ("ECU_A", "ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                state = gw.ecu_by_name(name).state
                self.assertIsNotNone(state.pending_seed)
                self.assertEqual(state.seed_counter, 1)

    def test_functional_seed_does_not_change_security_level_or_penalty(self):
        """副作用范围有限：不动 security_level、不消耗惩罚计数。"""
        gw = wired_gateway()
        gw.route(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        for name in ("ECU_A", "ECU_B", "ECU_C"):
            with self.subTest(ecu=name):
                state = gw.ecu_by_name(name).state
                self.assertEqual(state.security_level, uds_types.SECURITY_LOCKED)
                self.assertEqual(state.failed_attempts, 0)

    def test_functional_decisions_are_per_ecu(self):
        """C2-OD-4：策略在功能寻址下**逐 ECU** 求值。"""
        seen = []

        def policy(ecu, addressing_mode, sid):
            seen.append((ecu.request_id, addressing_mode, sid))
            return True

        gw = wired_gateway(policy=policy)
        gw.route(FUNCTIONAL_ADDRESS, REQUEST_SEED)

        self.assertEqual(
            seen,
            [
                (ECU_A_REQ, "functional", SID_SECURITY_ACCESS),
                (ECU_B_REQ, "functional", SID_SECURITY_ACCESS),
                (ECU_C_REQ, "functional", SID_SECURITY_ACCESS),
            ],
        )

    def test_c3_added_no_functional_special_case(self):
        """反向自证：C3 **未**为 functional 0x27 添加任何专门分支。"""
        import gateway as gateway_module
        import gateway_runtime

        for module in (gateway_module, gateway_runtime):
            source = read_source(module)
            with self.subTest(module=module.__name__):
                for token in ("FUNCTIONAL_ADDRESS ==", "is_functional", "functional_0x27"):
                    self.assertNotIn(token, source)


# ======================================================================
# provider 语义（C3-OD-14 / OQ-12）
# ======================================================================


class TestProviderSemantics(unittest.TestCase):
    """``{**SERVICES, **provider()}`` 的覆盖语义与限制。"""

    def test_same_sid_override_replaces_the_static_handler(self):
        """同名 SID 由 provider 覆盖。"""
        calls = []

        def provider():
            def fake_read(request, state):
                calls.append(request)
                return b"\x00"

            return {SID_READ_DID: fake_read}

        gw = build_default_gateway(services_provider=provider)
        results = gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))

        self.assertEqual(calls, [bytes([SID_READ_DID, 0xF1, 0x90])])
        self.assertEqual(results, [(ECU_A_RESP, b"\x00")])

    def test_unsampled_sids_keep_the_static_service(self):
        """未同名 SID 保留静态服务 —— provider 只提供 0x27 时其余不受影响。"""
        gw = wired_gateway()
        results = gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))
        self.assertEqual(results[0][1][:3], bytes([0x62, 0xF1, 0x90]))

    def test_provider_cannot_bypass_c1_guards_for_modelled_sids(self):
        """
        C3-OD-14 限制 2：对**已建模** SID，被替换的只是 handler，
        **授权 guard 仍由 resolve_service_spec 保留** —— 覆盖的是实现，不是授权。

        证据：用 provider 替换 0x2E 的 handler，但默认会话下仍必须被
        会话 guard 拦下（``7F 2E 7F``），而不是直接执行那个 handler。
        """
        called = []

        def provider():
            def fake_write(request, state):
                called.append(request)
                return b"\x6E\xF1\x02"

            return {0x2E: fake_write}

        gw = build_default_gateway(services_provider=provider)
        results = gw.route(ECU_A_REQ, bytes([0x2E, 0xF1, 0x02]) + b"XY")

        self.assertEqual(called, [], "guard 必须先行 —— handler 不得被执行")
        self.assertEqual(results[0][1], bytes([0x7F, 0x2E, 0x7F]))

    def test_provider_is_called_once_per_request(self):
        """provider 每次请求调用一次（不得缓存、也不得一次请求调用多次）。"""
        count = []

        def provider():
            count.append(1)
            return {}

        gw = build_default_gateway(services_provider=provider)
        gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))
        self.assertEqual(len(count), 1)

        gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))
        self.assertEqual(len(count), 2)

    def test_ecu_level_provider_takes_precedence(self):
        """
        ECU 级 provider 优先于网关级（保持既有 override 语义）。

        .. note::
           ``services_provider`` 与 ``handler`` 一样是**构造期**绑定
           （``__post_init__`` 用它绑定默认 handler）。构造后再赋值不会重新绑定 ——
           与既有 ``handler`` 字段的语义一致。
        """
        own = []

        def ecu_provider():
            own.append(1)
            return {}

        ecu = VirtualEcu(
            "ECU_A", ECU_A_REQ, ECU_A_RESP, services_provider=ecu_provider
        )
        gw = DiagnosticGateway([ecu], services_provider=make_provider(Clock()))
        gw.route(ECU_A_REQ, REQUEST_SEED)

        self.assertEqual(len(own), 1, "ECU 自带的 provider 应生效")
        self.assertEqual(ecu.services_provider, ecu_provider, "网关不得覆盖 ECU 级设置")

    def test_default_gateway_is_unwired_and_unchanged(self):
        """C3-OD-7：默认工厂不接线 ⇒ 默认网关与 C3 前行为相同。"""
        gw = build_default_gateway()
        self.assertTrue(all(ecu.services_provider is None for ecu in gw.ecus))
        self.assertEqual(
            gw.route(ECU_A_REQ, bytes([SID_READ_DID, 0xF1, 0x90]))[0][1][:3],
            bytes([0x62, 0xF1, 0x90]),
        )

    def test_gateway_does_not_know_about_clock_or_config(self):
        """C3-OD-3：**gateway.py 不得感知时钟与安全配置**（纯协议层）。"""
        import gateway as gateway_module

        source = read_source(gateway_module)
        for token in ("_now", "SECURITY_CONFIG", "monotonic", "seed_expiry_seconds"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


# ======================================================================
# 结构性不变量（沿用 C2-A4 的口径）
# ======================================================================


class TestStructuralInvariants(unittest.TestCase):
    """C3 不得引入第二套执行路径 / 第二套授权。"""

    def test_gateway_runtime_has_no_second_authorization_path(self):
        """C2-A4 F2 的延续：runtime 仍不得出现策略/寻址内部字段。"""
        import gateway_runtime

        source = read_source(gateway_runtime)
        for token in ("policy", "_allow", "_by_request", "PolicyDecision"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_uds_server_gained_exactly_one_public_binding_definition(self):
        """C3-OD-4：唯一绑定定义存在，且是纯函数（不读时钟 / 不读全局状态）。"""
        self.assertTrue(hasattr(uds_server, "security_access_services"))
        services = uds_server.security_access_services(
            now=BASE_CLOCK, security_config=dict(TEST_SECURITY_CONFIG)
        )
        self.assertEqual(set(services), {SID_SECURITY_ACCESS})
        self.assertTrue(callable(services[SID_SECURITY_ACCESS]))

    def test_binding_definition_does_not_touch_the_clock(self):
        import inspect

        source = inspect.getsource(uds_server.security_access_services)
        for token in ("time.", "monotonic", "_now"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_ecu_entry_still_delegates_to_the_single_definition(self):
        """C3-OD-4：直连 ECU 路径与新定义**同一份**，未各自实现。"""
        import inspect

        import ecu

        source = inspect.getsource(ecu._security_services)
        self.assertIn("uds_server.security_access_services", source)
        # 不得再直接调用底层工厂（那会形成第二份绑定路径）
        self.assertNotIn("_make_security_access_handler", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
