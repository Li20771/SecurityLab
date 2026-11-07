#!/usr/bin/env bash
# 诊断 vcan0 链路：先跑 uds-basic 基线 vcan 测试，再做原始 CAN 帧探测。
# 仅本地虚拟 CAN。
set -u
BASE=/mnt/d/SecurityLab
PY=/home/lilike/.venvs/network/bin/python
OUT="$BASE/labs/vehicle/ota/report/uds_basic_vcan_check.txt"

cd "$BASE/labs/vehicle/uds-basic"
timeout 90 "$PY" tests/test_transfer_vcan.py > "$OUT" 2>&1
echo "UDS_BASIC_EXIT=$?"
echo "--- output (first 60 lines + last 20) ---"
head -n 60 "$OUT"
echo "..."
tail -n 20 "$OUT"

echo "=== raw CAN probe ==="
if command -v cansend >/dev/null 2>&1 && command -v candump >/dev/null 2>&1; then
  candump -L vcan0 > /tmp/can_raw.log 2>&1 &
  DUMP_PID=$!
  sleep 0.3
  cansend vcan0 7E0#021001
  sleep 0.8
  kill "$DUMP_PID" 2>/dev/null
  wait "$DUMP_PID" 2>/dev/null
  echo "--- candump ---"
  cat /tmp/can_raw.log
else
  echo "can-utils 未安装"
fi
