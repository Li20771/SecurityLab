#!/usr/bin/env bash
# 对比诊断：uds-basic ECU vs OTA ECU 对同一原始 CAN 单帧的响应。
# 仅本地虚拟 CAN（vcan0）。
set -u
BASE=/mnt/d/SecurityLab
PY=/home/lilike/.venvs/network/bin/python
export PYTHONPATH="${GMSSL_SITE:-/mnt/c/Users/20771/AppData/Local/Python/pythoncore-3.14-64/Lib/site-packages}"

raw_probe() {
  local label="$1"
  candump -L vcan0 > /tmp/can_probe.log 2>&1 &
  local DUMP=$!
  sleep 0.3
  cansend vcan0 7E0#021001
  sleep 0.8
  kill "$DUMP" 2>/dev/null
  wait "$DUMP" 2>/dev/null
  echo "--- $label candump ---"
  cat /tmp/can_probe.log
}

echo "############ phase 1: uds-basic ecu.py ############"
cd "$BASE/labs/vehicle/uds-basic/ecu"
"$PY" -u ecu.py > /tmp/ecu_basic.log 2>&1 &
P1=$!
sleep 1
raw_probe "uds-basic"
kill "$P1" 2>/dev/null; wait "$P1" 2>/dev/null
echo "--- uds-basic log ---"
tail -n 6 /tmp/ecu_basic.log

echo "############ phase 2: OTA ota_ecu_boot.py ############"
cd "$BASE/labs/vehicle/ota"
PUB=$("$PY" -c 'import sys; sys.path.insert(0,"/mnt/d/SecurityLab/labs/crypto"); from gmcrypto.sm2 import sm2_keygen; print(sm2_keygen()[1].hex())')
"$PY" -u ota_ecu_boot.py --pub-hex "$PUB" > /tmp/ota_boot3.log 2>&1 &
P2=$!
sleep 1
raw_probe "ota"
kill "$P2" 2>/dev/null; wait "$P2" 2>/dev/null
echo "--- OTA log ---"
tail -n 8 /tmp/ota_boot3.log
