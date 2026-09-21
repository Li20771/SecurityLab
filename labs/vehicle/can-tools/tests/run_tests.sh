#!/usr/bin/env bash
#
# CAN / ISO-TP 实验工具测试入口
#
# 运行 can-tools 自身的测试：
#
#   1. test_parsers.py             纯解析逻辑（不需要 python-isotp / vcan0）
#   2. test_isotp_send_options.py  ISO-TP 参数解析与下发（需要 python-isotp，
#                                  但不打开 CAN socket，不需要 vcan0）
#   3. test_isotp_malformed_vcan.py 畸形 ISO-TP 分帧实验（需要 vcan0，
#                                  会自动启动 / 结束虚拟 ECU 子进程）
#
# 用法：
#
#   bash tests/run_tests.sh            # 全部
#   bash tests/run_tests.sh offline    # 只跑前两组（不需要 vcan0）
#   bash tests/run_tests.sh malformed  # 只跑畸形分帧实验（需要 vcan0）
#
# 环境变量：
#
#   PYTHON   指定 Python 解释器
#
# 安全边界：
#
#   只使用 vcan0（虚拟 CAN），只与本机虚拟 ECU 子进程通信。
#   不接触真实车辆 / 真实 ECU / 真实 CAN 适配器。

set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="${1:-all}"

VCAN_INTERFACE="vcan0"

PASS=0
WARN=0
FAIL=0

ok() {
    echo "[ OK ] $1"
    PASS=$((PASS + 1))
}

warn() {
    echo "[WARN] $1"
    WARN=$((WARN + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL=$((FAIL + 1))
}

if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON}" ]; then
    PYTHON_BIN="$PYTHON"
elif [ -x "$HOME/.venvs/network/bin/python" ]; then
    PYTHON_BIN="$HOME/.venvs/network/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "找不到 Python 解释器。" >&2
    exit 1
fi

echo "========================================"
echo " CAN / ISO-TP Tools Test Runner"
echo "========================================"
echo "Python: $PYTHON_BIN"
echo "Mode:   $MODE"
echo

run_offline() {
    if "$PYTHON_BIN" "$TESTS_DIR/test_parsers.py"; then
        ok "parser unit tests passed"
    else
        fail "parser unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_isotp_send_options.py"; then
        ok "ISO-TP option tests passed"
    else
        fail "ISO-TP option tests failed"
    fi

    echo

    if [ -f "$TESTS_DIR/test_fuzz_offline.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_fuzz_offline.py"; then
            ok "fuzz harness offline tests passed"
        else
            fail "fuzz harness offline tests failed"
        fi

        echo
    fi
}

run_malformed() {
    if ! ip link show "$VCAN_INTERFACE" >/dev/null 2>&1; then
        warn "$VCAN_INTERFACE 不存在，跳过畸形分帧实验"
        warn "请先启动 vcan0：sudo systemctl start vcan0.service"
        echo
        return
    fi

    if ! ip link show "$VCAN_INTERFACE" | grep -q "UP"; then
        warn "$VCAN_INTERFACE 存在但未 UP，跳过畸形分帧实验"
        echo
        return
    fi

    ok "$VCAN_INTERFACE 可用"

    if "$PYTHON_BIN" "$TESTS_DIR/test_isotp_malformed_vcan.py"; then
        ok "malformed ISO-TP experiments passed"
    else
        fail "malformed ISO-TP experiments failed"
    fi

    echo
}

case "$MODE" in
    offline)
        run_offline
        ;;
    malformed)
        run_malformed
        ;;
    all)
        run_offline
        run_malformed
        ;;
    *)
        echo "未知模式：$MODE（可用：all | offline | malformed）" >&2
        exit 2
        ;;
esac

echo "========================================"
echo " Summary"
echo "========================================"
echo "PASS: $PASS"
echo "WARN: $WARN"
echo "FAIL: $FAIL"
echo

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
