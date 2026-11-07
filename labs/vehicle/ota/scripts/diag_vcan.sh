#!/usr/bin/env bash
# OTA vcan0 e2e 诊断：启动 OTA ECU 子进程，用 isotp 客户端探测 10 01。
# 仅用于本地虚拟 CAN（vcan0），不接触真实设备。
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-/home/lilike/.venvs/network/bin/python}"
cd "$HERE"

export PYTHONPATH="${GMSSL_SITE:-/mnt/c/Users/20771/AppData/Local/Python/pythoncore-3.14-64/Lib/site-packages}"

PUB=$("$PY" -c 'import sys; sys.path.insert(0,"/mnt/d/SecurityLab/labs/crypto"); from gmcrypto.sm2 import sm2_keygen; print(sm2_keygen()[1].hex())')
echo "PUBLEN=${#PUB}"

"$PY" -u ota_ecu_boot.py --pub-hex "$PUB" > /tmp/ota_boot.log 2>&1 &
BOOT_PID=$!
sleep 1

"$PY" - > /tmp/ota_probe.log 2>&1 <<'PYEOF'
import sys, time
import isotp

def exchange(payload, timeout=1.5):
    sock = None
    try:
        sock = isotp.socket()
        sock.bind("vcan0", isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0x7E8, txid=0x7E0))
        sock.settimeout(timeout)
        sock.send(payload)
        return sock.recv()
    except Exception as exc:
        return ("EXC", type(exc).__name__, str(exc))
    finally:
        if sock is not None:
            try: sock.close()
            except Exception: pass

print("probe 10 01 ->", exchange(bytes([0x10, 0x01])))
PYEOF

echo "PROBE_EXIT=$?"
kill "$BOOT_PID" 2>/dev/null
wait "$BOOT_PID" 2>/dev/null
echo "=== boot.log ==="
cat /tmp/ota_boot.log
echo "=== probe.log ==="
cat /tmp/ota_probe.log
