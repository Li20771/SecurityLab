#!/bin/bash
# capture_lab.sh — CAN capture -> save -> replay 闭环（仅 vcan0）
#
# 段 A: CAN log   candump -L 保存 -> canplayer 回放 -> 再抓 -> 帧集合 diff
# 段 B: BLF       can_capture_blf record -> inspect(统计) -> replay -> 再抓 -> diff
# 段 C: PCAP      tshark -w 抓 socketcan pcap -> tshark -r 解析（Wireshark 生态）
#
# 全部使用虚拟总线 vcan0，不接触真实 CAN 接口。
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="$HOME/.venvs/network/bin/python"
WORK=/tmp/capture-lab
IFACE=vcan0
mkdir -p "$WORK"
rm -f "$WORK"/*.log "$WORK"/*.frames "$WORK"/*.blf "$WORK"/*.pcap 2>/dev/null

# 6 个已知标准帧（含 UDS 诊断请求/响应单帧与普通帧）
FRAMES=(
  "7E0#0210030000000000"
  "7E8#0650030032006400"
  "7E0#0322F19000000000"
  "7E8#0762F19056494E00"
  "123#AABBCCDDEEFF0011"
  "345#0102030405060708"
)

send_frames() {
  for f in "${FRAMES[@]}"; do
    cansend "$IFACE" "$f" || { echo "cansend failed: $f"; return 1; }
    sleep 0.05
  done
}

# 从 candump -L 文本里提取规范化的 ID#DATA 集合
norm_frames() {
  grep -oE '[0-9A-Fa-f]{3}#[0-9A-Fa-f]+' "$1" \
    | tr 'a-f' 'A-F' | sort -u
}

PASS_A=FAIL; PASS_B=FAIL; PASS_C=FAIL

echo "############ 段 A: CAN log (candump -L -> canplayer) ############"
candump -L "$IFACE" > "$WORK/send.log" 2>&1 &
P_CAP=$!
sleep 0.8
send_frames
sleep 0.8
kill "$P_CAP" 2>/dev/null; wait "$P_CAP" 2>/dev/null
norm_frames "$WORK/send.log" > "$WORK/send.frames"
echo "captured $(wc -l < "$WORK/send.frames") unique frames:"
cat "$WORK/send.frames"

candump -L "$IFACE" > "$WORK/replay.log" 2>&1 &
P_RECV=$!
sleep 0.8
canplayer -I "$WORK/send.log"
sleep 1.0
kill "$P_RECV" 2>/dev/null; wait "$P_RECV" 2>/dev/null
norm_frames "$WORK/replay.log" > "$WORK/replay.frames"
echo "replayed+recaptured $(wc -l < "$WORK/replay.frames") unique frames"
if diff -u "$WORK/send.frames" "$WORK/replay.frames"; then
  echo "CANLOG capture->save->replay IDENTITY = PASS"
  PASS_A=PASS
else
  echo "CANLOG IDENTITY = FAIL"
fi

echo
echo "############ 段 B: BLF (python-can record -> inspect -> replay) ############"
( cd "$ROOT" && "$PY" can_capture_blf.py record --out "$WORK/cap.blf" \
    --count 6 --max-seconds 12 > "$WORK/blf_record.log" 2>&1 ) &
P_BLF=$!
sleep 1.5
send_frames
wait "$P_BLF"; RC_REC=$?
echo "record rc=$RC_REC"
tail -2 "$WORK/blf_record.log"
( cd "$ROOT" && "$PY" can_capture_blf.py inspect "$WORK/cap.blf" --quiet ) | tee "$WORK/blf_inspect.log"

candump -L "$IFACE" > "$WORK/blf_recv.log" 2>&1 &
P_BRECV=$!
sleep 0.8
( cd "$ROOT" && "$PY" can_capture_blf.py replay "$WORK/cap.blf" )
sleep 1.0
kill "$P_BRECV" 2>/dev/null; wait "$P_BRECV" 2>/dev/null
norm_frames "$WORK/blf_recv.log" > "$WORK/blf_recv.frames"
if [ "$RC_REC" = 0 ] \
   && grep -q ': 6$' "$WORK/blf_inspect.log" \
   && diff -u "$WORK/send.frames" "$WORK/blf_recv.frames"; then
  echo "BLF record->inspect(count=6)->replay IDENTITY = PASS"
  PASS_B=PASS
else
  echo "BLF IDENTITY = FAIL"
fi

echo
echo "############ 段 C: PCAP (tshark socketcan capture -> parse) ############"
tshark -i "$IFACE" -w "$WORK/cap.pcap" -c 6 -a duration:10 > "$WORK/tshark_cap.log" 2>&1 &
P_TS=$!
sleep 1.5
send_frames
wait "$P_TS"; RC_TS=$?
echo "tshark capture rc=$RC_TS"
if [ -s "$WORK/cap.pcap" ]; then
  N=$(tshark -r "$WORK/cap.pcap" -T fields -e can.id 2>/dev/null | grep -c . )
  echo "pcap CAN frames parsed by tshark: $N"
  tshark -r "$WORK/cap.pcap" -T fields -e can.id -e can.data 2>/dev/null | head -6
  if [ "$N" -ge 6 ]; then
    echo "PCAP capture->save->parse = PASS (PCAP 用于 Wireshark 分析；CAN 回放用 CAN log/BLF)"
    PASS_C=PASS
  fi
else
  echo "pcap not created (tshark/dumpcap 权限? 见 $WORK/tshark_cap.log)"
fi

echo
echo "############ 汇总 ############"
echo "CAN_LOG : $PASS_A"
echo "BLF     : $PASS_B"
echo "PCAP    : $PASS_C"
if [ "$PASS_A" = PASS ] && [ "$PASS_B" = PASS ] && [ "$PASS_C" = PASS ]; then
  echo "CAPTURE_LAB=PASS"
else
  echo "CAPTURE_LAB=CHECK"
fi