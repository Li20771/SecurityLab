#!/usr/bin/env bash
#
# SecurityLab —— 扩展安全测试门禁（extended，需 vcan0）
#
# 包含需要虚拟 CAN 总线（SocketCAN/ISO-TP）的端到端测试与安全工具演练：
#
#   1. UDS 全套（离线单元 + vcan0 端到端：会话/安全访问/0x2E/0x3E/多ECU）
#   2. CAN tools 全套（离线 + 畸形 ISO-TP 分帧实验，需 vcan0）
#   3. capture → save → replay 闭环（CAN log / BLF / PCAP 三格式）
#   4. YARA 规则：真阳性 / 真阴性断言
#
# 可选：
#   bash scripts/qa_extended.sh --fuzz   追加 CAN fuzz smoke(30) 长测
#
# 重要：这些测试**独占 vcan0**，不要与其它 ECU 进程 / fuzz / healthcheck
#       --extended 并发运行，否则会因总线抢占出现 ISO-TP EILSEQ 等假失败。
#
# 前置：vcan0 已存在并 UP。若缺失，本脚本在有 root / 免密 sudo 时会尝试创建。
# 退出码：0 = 全过；非 0 = 至少一项失败。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IFACE="vcan0"
RUN_FUZZ=0

for arg in "$@"; do
    case "$arg" in
        --fuzz) RUN_FUZZ=1 ;;
        *) echo "未知参数：$arg（可用：--fuzz）" >&2; exit 2 ;;
    esac
done

FAIL=0

step() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

# ----------------------------------------------------------------------
# 前置：确保 vcan0 存在且 UP
# ----------------------------------------------------------------------
if [ ! -e "/sys/class/net/$IFACE" ]; then
    echo "[setup] $IFACE 不存在，尝试创建（需要 root / CAP_NET_ADMIN）..."
    if [ "$(id -u)" -eq 0 ]; then
        modprobe vcan 2>/dev/null || true
        ip link add dev "$IFACE" type vcan
        ip link set "$IFACE" up
    elif sudo -n true 2>/dev/null; then
        sudo modprobe vcan 2>/dev/null || true
        sudo ip link add dev "$IFACE" type vcan
        sudo ip link set "$IFACE" up
    else
        echo "[FAIL] $IFACE 不存在且当前环境无法免密创建。请先执行："
        echo "       sudo modprobe vcan && sudo ip link add dev vcan0 type vcan"
        echo "       && sudo ip link set vcan0 up"
        exit 2
    fi
fi

STATE="$(cat "/sys/class/net/$IFACE/operstate" 2>/dev/null || echo unknown)"
case "$STATE" in
    up|unknown) echo "[setup] $IFACE operstate=$STATE" ;;
    *)
        echo "[setup] $IFACE operstate=$STATE，尝试 ip link set up"
        if [ "$(id -u)" -eq 0 ]; then
            ip link set "$IFACE" up
        elif sudo -n true 2>/dev/null; then
            sudo ip link set "$IFACE" up
        else
            echo "[FAIL] $IFACE 未 UP 且无法提权，请手动：sudo ip link set vcan0 up"
            exit 2
        fi
        ;;
esac

step "[1/4] UDS 全套测试（单元 + vcan0 端到端 + 多 ECU）"
if bash "$ROOT/labs/vehicle/uds-basic/tests/run_tests.sh" all; then
    echo "[ OK ] uds all"
else
    echo "[FAIL] uds all"; FAIL=1
fi

step "[2/4] CAN tools 全套（离线 + 畸形 ISO-TP 分帧）"
if bash "$ROOT/labs/vehicle/can-tools/tests/run_tests.sh" all; then
    echo "[ OK ] can-tools all"
else
    echo "[FAIL] can-tools all"; FAIL=1
fi

step "[3/4] CAN capture → save → replay 闭环（log/BLF/PCAP）"
if bash "$ROOT/labs/vehicle/can-tools/capture/capture_lab.sh"; then
    echo "[ OK ] capture lab"
else
    echo "[FAIL] capture lab"; FAIL=1
fi

step "[4/4] YARA 真阳性 / 真阴性演练"
if bash "$ROOT/labs/yara-basic/scan.sh"; then
    echo "[ OK ] yara"
else
    echo "[FAIL] yara"; FAIL=1
fi

if [ "$RUN_FUZZ" -eq 1 ]; then
    step "[可选] CAN/ISO-TP fuzz smoke(30)"
    if "$HOME/.venvs/network/bin/python" \
            "$ROOT/labs/vehicle/can-tools/fuzz/isotp_fuzz.py" --smoke; then
        echo "[ OK ] fuzz smoke"
    else
        echo "[FAIL] fuzz smoke"; FAIL=1
    fi
fi

echo
echo "================================================================"
if [ "$FAIL" -eq 0 ]; then
    echo "  QA EXTENDED RESULT: PASS"
else
    echo "  QA EXTENDED RESULT: FAIL"
fi
echo "================================================================"
exit "$FAIL"
