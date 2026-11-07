#!/usr/bin/env bash
# 精确对比诊断：同一 isotp 客户端探测 uds-basic ECU 与 OTA ECU。
set -u
BASE=/mnt/d/SecurityLab
PY=/home/lilike/.venvs/network/bin/python
export PYTHONPATH="${GMSSL_SITE:-/mnt/c/Users/20771/AppData/Local/Python/pythoncore-3.14-64/Lib/site-packages}"

probe() {
  "$PY" - <<'PYEOF' 2>&1
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
}

echo "== uds-basic ecu =="
cd "$BASE/labs/vehicle/uds-basic/ecu"
"$PY" -u ecu.py > /tmp/d1.log 2>&1 &
P=$!
sleep 2
echo "alive=$([ -d /proc/$P ] && echo yes || echo no)"
probe
echo "log:"; cat /tmp/d1.log
kill "$P" 2>/dev/null; wait "$P" 2>/dev/null

echo "== ota ecu =="
cd "$BASE/labs/vehicle/ota"
PUB=$("$PY" -c 'import sys; sys.path.insert(0,"/mnt/d/SecurityLab/labs/crypto"); from gmcrypto.sm2 import sm2_keygen; print(sm2_keygen()[1].hex())')
"$PY" -u ota_ecu_boot.py --pub-hex "$PUB" > /tmp/d2.log 2>&1 &
P=$!
sleep 2
echo "alive=$([ -d /proc/$P ] && echo yes || echo no)"
probe
echo "log:"; cat /tmp/d2.log
kill "$P" 2>/dev/null; wait "$P" 2>/dev/null
