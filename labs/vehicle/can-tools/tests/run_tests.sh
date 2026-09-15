#!/usr/bin/env bash
#
# CAN / ISO-TP 实验工具测试入口
#
# 运行 can-tools 自身的测试：
#
#   1. test_parsers.py             纯解析逻辑（不需要 python-isotp / vcan0）
#   2. test_isotp_send_options.py  ISO-TP 参数解析与下发（需要 python-isotp，
#                                  但不打开 CAN socket，不需要 vcan0）
#
# 用法：
#
#   bash tests/run_tests.sh
#
# 环境变量：
#
#   PYTHON   指定 Python 解释器
#
# 安全边界：
#
#   本脚本只跑离线测试，不打开 CAN socket，不接触真实车辆 / 真实 ECU。
#   需要 vcan0 的在网实验见 ../../uds-basic/tests/test_isotp_options_vcan.py。

set -u

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASS=0
FAIL=0

ok() {
    echo "[ OK ] $1"
    PASS=$((PASS + 1))
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
echo

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
echo "========================================"
echo " Summary"
echo "========================================"
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

exit 0
