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
WORK=/tmp/capture-lab
IFACE=vcan0

# ----------------------------------------------------------------------
# Python 解释器选择
# ----------------------------------------------------------------------
# 历史缺陷：这里曾**硬编码** `$HOME/.venvs/network/bin/python`。
# 该路径只在本地 WSL 开发机上存在；在任何其它环境（含 CI runner）上
# 都不存在，导致段 B（BLF）必然失败。
#
# 更严重的是：段 B 失败时脚本仍以 0 退出、并打印 CAPTURE_LAB=CHECK，
# 使调用方（scripts/qa_extended.sh，它按退出码判定）把一次**失败的**
# capture 环节当成通过。这正是 OQ-2d 要修的"失败不上报"缺陷。
#
# 现在的选择顺序：$PYTHON 覆盖 > venv > PATH 中的 python3 > python。
# 解析不到解释器时**直接失败**（exit 2），而不是继续跑出假结果。
pick_python() {
  if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON}" ]; then
    printf '%s\n' "$PYTHON"; return 0
  fi
  if [ -x "$HOME/.venvs/network/bin/python" ]; then
    printf '%s\n' "$HOME/.venvs/network/bin/python"; return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3; return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python; return 0
  fi
  return 1
}

if ! PY="$(pick_python)"; then
  echo "[FAIL] 找不到 Python 解释器（试过 \$PYTHON / venv / python3 / python）" >&2
  exit 2
fi
echo "python: $PY"

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

# ----------------------------------------------------------------------
# 退出码传播（OQ-2d 核心修复）
# ----------------------------------------------------------------------
# 历史缺陷：无论三个段结果如何，脚本最终都执行 `echo`，因此**恒以 0 退出**。
# 调用方 scripts/qa_extended.sh:95 是按**退出码**判定的：
#
#     if bash .../capture_lab.sh; then echo "[ OK ] capture lab"
#     else echo "[FAIL] capture lab"; FAIL=1; fi
#
# 于是"BLF 段失败 + CAPTURE_LAB=CHECK"会被上报成 `[ OK ] capture lab`，
# 一次真实失败被汇总成通过。这是 fail-open，必须消除。
#
# 修复后的语义：
#   PASS   —— 三段全过                     → 退出 0
#   FAIL   —— 任一段未过                   → 退出 1（调用方将记 FAIL）
#   ERR    —— 环境无法运行（无解释器等）    → 退出 2（上面已 exit 2）
#
# 注意：仍**保留** CAPTURE_LAB=PASS/CHECK 这行人类可读汇总，便于日志检索；
# 但它不再与退出码脱钩。
if [ "$PASS_A" = PASS ] && [ "$PASS_B" = PASS ] && [ "$PASS_C" = PASS ]; then
  echo "CAPTURE_LAB=PASS"
  exit 0
fi

echo "CAPTURE_LAB=CHECK"
echo "[FAIL] capture lab 存在未通过段（CAN_LOG=$PASS_A BLF=$PASS_B PCAP=$PASS_C）" >&2
exit 1