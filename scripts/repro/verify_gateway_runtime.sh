#!/usr/bin/env bash
#
# verify_gateway_runtime.sh —— Gateway Runtime v1（OQ7）最小可复现入口
#
# 本脚本是 `labs/vehicle/uds-basic/ecu/gateway_runtime.py` 的**最小闭环**：
# 启动守护进程 → 在真实 vcan0 上做一次端到端验证 → 干净收尾。
# 完整测试矩阵（unit + 20 项 e2e）在
# `labs/vehicle/uds-basic/tests/test_gateway_runtime_{unit,vcan}.py`，
# 由 `tests/run_tests.sh` 统一驱动；本脚本**不**重复那套矩阵。
#
# 检查项（对应 OQ7 的核心能力，逐条独立断言）：
#
#   1. 守护进程启动并就绪（以真实请求探测，不是 sleep 猜测）
#   2. 物理寻址：0x7E0→0x7E8 / 0x7E1→0x7E9 / 0x7E2→0x7EA（byte-exact）
#   3. 多帧：20 字节 VIN 响应经 ISO-TP 首帧+连续帧重组（而非只"收到东西"）
#   4. 功能寻址：0x7DF 广播使三个 ECU 都切换会话，且**总线上零响应帧**
#   5. 未知物理 ID：总线上除 tester 自己的请求外零帧
#   6. 畸形请求：被回答以 NRC，且守护进程继续存活并正常服务
#   7. 生命周期：SIGTERM → 退出码 0（优雅退出）+ 不留残余进程
#
# 第 4/5 项刻意用**原始 CAN 抓包**判定：ISO-TP 客户端只能听到自己绑定的那一个
# CAN ID，"总线静默"必须在总线层看全部帧才能成立。
#
# 依赖：vcan0 已存在且 UP（**独占**，勿与其它 CAN 测试并行）；
#       Python venv 含 python-can / can-isotp。
# 产物全部写入 mktemp 临时目录，结束即删除；不提交生成物。
#
# 退出码：0 = 全部通过；1 = 有检查失败；2 = 环境不满足（不算假 PASS）。
#
# 安全边界：只使用 vcan0（虚拟 CAN），不接触真实车辆 / 真实 ECU / 真实
# CAN 适配器。

set -u

VCAN="vcan0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_SCRIPT="$REPO_ROOT/labs/vehicle/uds-basic/ecu/gateway_runtime.py"
ECU_DIR="$REPO_ROOT/labs/vehicle/uds-basic/ecu"

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

WORK="$(mktemp -d)"
RUNTIME_PID=""

cleanup() {
    if [ -n "$RUNTIME_PID" ] && kill -0 "$RUNTIME_PID" 2>/dev/null; then
        kill -TERM "$RUNTIME_PID" 2>/dev/null

        for _ in $(seq 1 50); do
            kill -0 "$RUNTIME_PID" 2>/dev/null || break
            sleep 0.1
        done

        # 兜底：绝不能因为脚本失败就把守护进程留在后台
        kill -KILL "$RUNTIME_PID" 2>/dev/null
    fi

    rm -rf "$WORK"
}
trap cleanup EXIT

echo "========================================"
echo " Gateway Runtime v1 —— 最小可复现验证"
echo "========================================"
echo "workdir: $WORK"
echo

# ----------------------------------------------------------------------
# 依赖检查（缺失 = 环境不可用 → exit 2，不是验证失败）
# ----------------------------------------------------------------------

if [ ! -f "$RUNTIME_SCRIPT" ]; then
    echo "[MISS] 未找到 $RUNTIME_SCRIPT"
    exit 2
fi

if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON}" ]; then
    PYTHON_BIN="$PYTHON"
elif [ -x "$HOME/.venvs/network/bin/python" ]; then
    PYTHON_BIN="$HOME/.venvs/network/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "[MISS] Python 解释器未找到"
    exit 2
fi

if ! ip link show "$VCAN" >/dev/null 2>&1; then
    echo "[MISS] $VCAN 不存在（先：sudo systemctl start vcan0.service）"
    exit 2
fi

if ! ip link show "$VCAN" | grep -q "UP"; then
    echo "[MISS] $VCAN 存在但未 UP"
    exit 2
fi

ok "$VCAN UP（本脚本独占总线，请勿并发其它 CAN 测试）"

if ! "$PYTHON_BIN" -c "import can, isotp" >/dev/null 2>&1; then
    echo "[MISS] venv 缺少 python-can / can-isotp"
    exit 2
fi

ok "Python venv：python-can / can-isotp 可导入（$PYTHON_BIN）"

# ----------------------------------------------------------------------
# 1. 启动守护进程
# ----------------------------------------------------------------------

# --wait-timeout 0.3：让 SIGTERM 的退出延迟足够小，便于本脚本快速收尾。
"$PYTHON_BIN" -u "$RUNTIME_SCRIPT" --interface "$VCAN" --wait-timeout 0.3 \
    > "$WORK/runtime.log" 2>&1 &
RUNTIME_PID=$!

if "$PYTHON_BIN" - "$VCAN" <<'PY' > "$WORK/ready.out" 2>&1
import sys
import time

import isotp

interface = sys.argv[1]
IFACE_RX, IFACE_TX = 0x7E8, 0x7E0

#: 22 F1 90 的确定性正响应（VIN 出厂值 17 字节）。
VIN_RESPONSE = bytes([0x62, 0xF1, 0x90]) + b"TESTECU0000000001"

deadline = time.monotonic() + 12.0

while time.monotonic() < deadline:
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            interface,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits, rxid=IFACE_RX, txid=IFACE_TX
            ),
        )
        sock.settimeout(1.0)
        sock.send(bytes([0x22, 0xF1, 0x90]))

        if sock.recv() == VIN_RESPONSE:
            print("ready")
            sys.exit(0)
    except Exception:  # noqa: BLE001 - 未就绪时各种异常都按"还没起来"处理
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    time.sleep(0.3)

print("not ready within 12s")
sys.exit(1)
PY
then
    ok "守护进程已启动并就绪（PID $RUNTIME_PID）"
else
    fail "守护进程未在 12s 内就绪"
    echo "--- runtime.log ---"
    cat "$WORK/runtime.log"
fi

# ----------------------------------------------------------------------
# 2–6. 端到端检查（真实 ISO-TP + 原始 CAN 抓包）
# ----------------------------------------------------------------------

if "$PYTHON_BIN" - "$VCAN" <<'PY' > "$WORK/e2e.out" 2>&1
"""
最小端到端检查。全部断言在 Python 内完成，退出码即结论。
"""
import sys
import time

import can
import isotp

interface = sys.argv[1]

ECU_A = (0x7E0, 0x7E8)
ECU_B = (0x7E1, 0x7E9)
ECU_C = (0x7E2, 0x7EA)
PHYSICAL = (ECU_A, ECU_B, ECU_C)

FUNCTIONAL_ID = 0x7DF
UNKNOWN_ID = 0x7E5
UNKNOWN_RESPONSE_ID = 0x7ED

DID_F102_REQUEST = bytes([0x22, 0xF1, 0x02])
DID_F102_WRITE = bytes([0x2E, 0xF1, 0x02])
DEFAULT_CONFIG = b"DEFAULT_CONFIG"

READ_VIN = bytes([0x22, 0xF1, 0x90])
VIN_RESPONSE = bytes([0x62, 0xF1, 0x90]) + b"TESTECU0000000001"

SILENCE_WINDOW = 1.0

checks = []


def record(name, passed, detail=""):
    checks.append((name, passed, detail))
    print(f"{'OK  ' if passed else 'FAIL'} {name}{' — ' + detail if detail else ''}")


def exchange(request_id, response_id, payload, timeout=1.5):
    sock = None
    try:
        sock = isotp.socket()
        sock.bind(
            interface,
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=response_id,
                txid=request_id,
            ),
        )
        sock.settimeout(timeout)
        sock.send(payload)

        return sock.recv()
    except Exception:  # noqa: BLE001 - 超时/传输错误统一按"无响应"处理
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


class Sniffer:
    """原始 CAN 抓包（能看见总线上的全部帧）。"""

    def __init__(self):
        self.bus = can.interface.Bus(interface="socketcan", channel=interface)
        self.frames = []

        collected = self

        class Collector(can.Listener):
            def on_message_received(self, msg):
                collected.frames.append((msg.arbitration_id, bytes(msg.data)))

        self._notifier = can.Notifier(self.bus, [Collector()])

    def drain(self):
        time.sleep(0.2)
        self.frames.clear()

    def stop(self):
        self._notifier.stop()
        self.bus.shutdown()
        return self.frames

    def ids(self):
        return {can_id for can_id, _ in self.frames}

    def frames_on(self, can_id):
        return [data for frame_id, data in self.frames if frame_id == can_id]


# ---- 2. 物理寻址 ----
for request_id, response_id in PHYSICAL:
    response = exchange(request_id, response_id, READ_VIN)
    record(
        f"物理寻址 0x{request_id:03X} → 0x{response_id:03X}",
        response == VIN_RESPONSE,
        f"实际 {response.hex(' ') if response else 'None'}",
    )

# ---- 3. 多帧：20 字节响应必须经首帧 + 连续帧 ----
sniffer = Sniffer()
sniffer.drain()
exchange(*ECU_A, READ_VIN)
frames = sniffer.stop()

pcis = [data[0] & 0xF0 for data in sniffer.frames_on(ECU_A[1]) if data]
record(
    "多帧响应经 FF+CF 分帧（0x7E8）",
    0x10 in pcis and 0x20 in pcis,
    f"PCI 类型 {[hex(p) for p in pcis]}",
)
record(
    "应答不串到其它应答 ID",
    not ({ECU_B[1], ECU_C[1]} & {can_id for can_id, _ in frames}),
    f"总线 CAN ID {sorted(hex(i) for i in {can_id for can_id, _ in frames})}",
)

# ---- 4. 功能寻址：三个 ECU 都执行，但总线零响应 ----
before = {
    physical: exchange(*physical, DID_F102_WRITE + b"X") for physical in PHYSICAL
}
record(
    "广播前三个 ECU 均在默认会话（写 DID → 0x7F）",
    all(r == bytes([0x7F, 0x2E, 0x7F]) for r in before.values()),
    f"实际 {[r.hex(' ') if r else None for r in before.values()]}",
)

sniffer = Sniffer()
sniffer.drain()
broadcast = exchange(FUNCTIONAL_ID, ECU_A[1], bytes([0x10, 0x03]), timeout=SILENCE_WINDOW)
frames = sniffer.stop()

record("功能寻址 0x7DF 不回帧（tester 侧）", broadcast is None,
       f"实际 {broadcast.hex(' ') if broadcast else 'None'}")

leaked = [(can_id, data) for can_id, data in frames if can_id != FUNCTIONAL_ID]
record(
    "功能寻址后总线除广播请求外零帧",
    not leaked,
    f"额外帧 {[(hex(i), d.hex(' ')) for i, d in leaked]}",
)

after = {
    physical: exchange(*physical, DID_F102_WRITE + b"X") for physical in PHYSICAL
}
record(
    "广播确实到达三个 ECU（extended+locked → 0x33）",
    all(r == bytes([0x7F, 0x2E, 0x33]) for r in after.values()),
    f"实际 {[r.hex(' ') if r else None for r in after.values()]}",
)

# 复位到默认会话，避免影响后续检查
for physical in PHYSICAL:
    exchange(*physical, bytes([0x11, 0x01]))

# ---- 5. 未知物理 ID：总线静默 ----
sniffer = Sniffer()
sniffer.drain()
unknown = exchange(UNKNOWN_ID, UNKNOWN_RESPONSE_ID, bytes([0x3E, 0x00]), timeout=SILENCE_WINDOW)
frames = sniffer.stop()

record("未知物理 ID 无响应", unknown is None,
       f"实际 {unknown.hex(' ') if unknown else 'None'}")
record(
    "未知物理 ID 总线只出现 tester 自己的请求",
    sniffer.ids() == {UNKNOWN_ID},
    f"总线 CAN ID {sorted(hex(i) for i in sniffer.ids())}",
)

# ---- 6. 畸形请求：NRC + 守护进程存活 ----
MALFORMED = [
    (bytes([0x22]), bytes([0x7F, 0x22, 0x13])),
    (bytes([0x3E]), bytes([0x7F, 0x3E, 0x13])),
    (bytes([0xFF]), bytes([0x7F, 0xFF, 0x11])),
]

malformed_ok = True
for payload, expected in MALFORMED:
    got = exchange(*ECU_A, payload)
    if got != expected:
        malformed_ok = False
        print(f"     {payload.hex(' ')} → {got.hex(' ') if got else None}（期望 {expected.hex(' ')}）")

record("畸形请求被回答以 NRC", malformed_ok)

record(
    "畸形请求后仍能正常服务",
    exchange(*ECU_A, READ_VIN) == VIN_RESPONSE,
)

passed = sum(1 for _, ok_, _ in checks if ok_)
failed = len(checks) - passed
print()
print(f"checks_passed={passed} checks_failed={failed}")

sys.exit(1 if failed else 0)
PY
then
    ok "端到端检查全部通过"
else
    fail "端到端检查存在失败项"
fi

cat "$WORK/e2e.out"

# ----------------------------------------------------------------------
# 7. 生命周期：SIGTERM → 退出码 0，且不留残余进程
# ----------------------------------------------------------------------

if [ -n "$RUNTIME_PID" ] && kill -0 "$RUNTIME_PID" 2>/dev/null; then
    kill -TERM "$RUNTIME_PID" 2>/dev/null

    wait "$RUNTIME_PID"
    RUNTIME_RC=$?

    if [ "$RUNTIME_RC" -eq 0 ]; then
        ok "SIGTERM 后优雅退出（退出码 0）"
    else
        fail "SIGTERM 后退出码 $RUNTIME_RC（0 = 优雅退出；负值 = 被信号直接杀死）"
    fi

    if kill -0 "$RUNTIME_PID" 2>/dev/null; then
        fail "守护进程在 SIGTERM 后仍然存活"
    else
        ok "无残余守护进程"
    fi

    if grep -q "已停止" "$WORK/runtime.log"; then
        ok "退出时打印了清理完成信息"
    else
        fail "退出时未打印清理完成信息"
    fi
else
    fail "守护进程在生命周期检查前已消失"
fi

RUNTIME_PID=""

# ----------------------------------------------------------------------

echo
echo "========================================"
echo " Gateway Runtime v1 summary"
echo "========================================"
echo "PASS: $PASS"
echo "FAIL: $FAIL"

if [ "$FAIL" -gt 0 ]; then
    echo
    echo "--- runtime.log ---"
    [ -f "$WORK/runtime.log" ] && cat "$WORK/runtime.log"
    exit 1
fi

exit 0
