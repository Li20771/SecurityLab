#!/usr/bin/env python3
"""
UDS Basic —— vcan0 上的多 ECU 物理寻址实时集成测试。

在同一条 vcan0 上启动三个**独立的 ECU 进程**，分别绑定不同的
物理请求 / 响应标识符（ecu.py 通过 UDS_RX_ID / UDS_TX_ID 环境变量配置）：

    ECU_A  request 0x7E0 / response 0x7E8
    ECU_B  request 0x7E1 / response 0x7E9
    ECU_C  request 0x7E2 / response 0x7EA

本测试验证：

1. 物理寻址：每个请求只被目标 ECU 应答，且响应出现在**该 ECU 自己**的
   响应标识符上（路由 / 寻址正确）。
2. 状态隔离：对 ECU_A 切到 extended 会话后，ECU_B / ECU_C 仍处于
   default 会话（用 0x2E 的门控 NRC 差异来证明：extended+locked → 0x33，
   default → 0x7F）。
3. 每个 ECU 独立响应 TesterPresent。

说明：功能寻址 0x7DF 的**实时广播转发**需要一个常驻网关守护进程，
其协议层路由语义已由 test_gateway_unit.py 完整覆盖；实时守护进程在
路线图中，本测试不涉及 0x7DF。

只使用 vcan0，不接触真实 CAN / ECU / 车辆。

运行：

    python3 tests/test_multi_ecu_vcan.py
或：
    bash tests/run_tests.sh all
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ECU_DIR = os.path.join(os.path.dirname(_HERE), "ecu")
_ECU_SCRIPT = os.path.join(_ECU_DIR, "ecu.py")

import isotp  # noqa: E402

INTERFACE = os.environ.get("UDS_INTERFACE", "vcan0")
READY_TIMEOUT = 12.0

# (name, ECU 接收的请求 ID = tester 发送 ID, ECU 发送的响应 ID)
ECUS = [
    ("ECU_A", 0x7E0, 0x7E8),
    ("ECU_B", 0x7E1, 0x7E9),
    ("ECU_C", 0x7E2, 0x7EA),
]

_PROCS = {}


def exchange(request_id: int, response_id: int, payload: bytes,
             timeout: float = 1.5):
    """以 (tx=request_id, rx=response_id) 新建 ISO-TP 连接收发；超时 None。"""
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            INTERFACE,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=response_id,
                txid=request_id,
            ),
        )
        sock.settimeout(timeout)
        sock.send(payload)
        return sock.recv()
    except Exception:  # noqa: BLE001
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def _start_one(name: str, request_id: int, response_id: int) -> None:
    log = tempfile.NamedTemporaryFile(
        prefix=f"ecu-{name.lower()}-", suffix=".log", delete=False
    )
    env = os.environ.copy()
    env["UDS_INTERFACE"] = INTERFACE
    env["UDS_RX_ID"] = f"0x{request_id:X}"
    env["UDS_TX_ID"] = f"0x{response_id:X}"

    proc = subprocess.Popen(
        [sys.executable, "-u", _ECU_SCRIPT],
        cwd=_ECU_DIR,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    _PROCS[name] = proc

    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        resp = exchange(request_id, response_id, bytes([0x10, 0x01]), timeout=1.0)
        if resp is not None and resp and resp[0] == 0x50:
            return
        time.sleep(0.3)

    raise RuntimeError(f"{name} 在 {READY_TIMEOUT}s 内未就绪（日志 {log.name}）")


def _start_all() -> None:
    for name, req, resp in ECUS:
        _start_one(name, req, resp)


def _stop_all() -> None:
    for proc in list(_PROCS.values()):
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    pass
    _PROCS.clear()


class MultiEcuTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _start_all()

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_all()

    def test_each_ecu_responds_on_its_own_response_id(self):
        for name, req, resp in ECUS:
            with self.subTest(ecu=name):
                # 22 F1 90：公开 DID，默认会话可读
                r = exchange(req, resp, bytes([0x22, 0xF1, 0x90]))
                self.assertIsNotNone(r, f"{name} 未响应")
                self.assertEqual(r[0], 0x62)
                self.assertEqual((r[1], r[2]), (0xF1, 0x90))

    def test_tester_present_per_ecu(self):
        for name, req, resp in ECUS:
            with self.subTest(ecu=name):
                r = exchange(req, resp, bytes([0x3E, 0x00]))
                self.assertEqual(r, bytes([0x7E, 0x00]), f"{name} TesterPresent 异常")

    def test_session_state_is_independent_between_ecus(self):
        a_req, a_resp = 0x7E0, 0x7E8
        b_req, b_resp = 0x7E1, 0x7E9

        # 只把 ECU_A 切到 extended（10 03 → 50 03）
        r = exchange(a_req, a_resp, bytes([0x10, 0x03]))
        self.assertIsNotNone(r)
        self.assertEqual(r[0], 0x50)
        self.assertEqual(r[1], 0x03)

        # ECU_A 处于 extended 但未解锁：写 DID → 7F 2E 33（securityAccessDenied）
        r_a = exchange(a_req, a_resp, bytes([0x2E, 0xF1, 0x02]) + b"X")
        self.assertEqual(r_a, bytes([0x7F, 0x2E, 0x33]),
                         "ECU_A 应为 extended+locked(0x33)")

        # ECU_B 从未切会话，仍是 default：写 DID → 7F 2E 7F
        # （serviceNotSupportedInActiveSession）
        r_b = exchange(b_req, b_resp, bytes([0x2E, 0xF1, 0x02]) + b"X")
        self.assertEqual(r_b, bytes([0x7F, 0x2E, 0x7F]),
                         "ECU_B 应仍为 default(0x7F)，状态未与 ECU_A 隔离")

        # ECU_C 同样仍是 default
        r_c = exchange(0x7E2, 0x7EA, bytes([0x2E, 0xF1, 0x02]) + b"X")
        self.assertEqual(r_c, bytes([0x7F, 0x2E, 0x7F]))

    def test_wrong_address_gets_no_response(self):
        # 0x7E5 上没有 ECU：不应收到响应（超时 None）
        r = exchange(0x7E5, 0x7ED, bytes([0x3E, 0x00]), timeout=1.0)
        self.assertIsNone(r, "不存在的物理地址不应有 ECU 应答")


if __name__ == "__main__":
    unittest.main(verbosity=2)
