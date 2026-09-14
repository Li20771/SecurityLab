#!/usr/bin/env bash

set -u

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

echo "========================================"
echo " SecurityLab Environment Health Check"
echo "========================================"
echo

echo "=== System ==="

if command -v wslpath >/dev/null 2>&1; then
    ok "WSL environment detected"
else
    warn "wslpath not found"
fi

if systemctl is-system-running 2>/dev/null | grep -q '^running$'; then
    ok "systemd running"
else
    warn "systemd not fully running"
fi

echo
echo "=== Git ==="

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    ok "SecurityLab Git repository"
else
    fail "Not inside a Git repository"
fi

if git status --porcelain | grep -q .; then
    warn "Git working tree has changes"
else
    ok "Git working tree clean"
fi

echo
echo "=== DSH ==="

if command -v dsh >/dev/null 2>&1; then
    ok "dsh command available"
    echo "      version: $(dsh --version 2>/dev/null | head -1)"
else
    fail "dsh command not found"
fi

echo
echo "=== Python ==="

PYTHON_BIN=""

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [ -x "$HOME/.venvs/network/bin/python" ]; then
    PYTHON_BIN="$HOME/.venvs/network/bin/python"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
fi

if [ -n "$PYTHON_BIN" ]; then
    ok "Python available"
    echo "      interpreter: $PYTHON_BIN"
    echo "      version: $($PYTHON_BIN --version 2>&1)"
else
    fail "Python interpreter not found"
fi

PYTHON_REQUIREMENTS="$PROJECT_ROOT/configs/environment/python-network-requirements.txt"

python_check() {
    local package="$1"
    local module="$2"
    local label="$3"
    local expected="$4"

    if [ -z "$PYTHON_BIN" ]; then
        fail "$label unavailable"
        return
    fi

    if ! "$PYTHON_BIN" -c "import $module" >/dev/null 2>&1; then
        fail "$label unavailable"
        return
    fi

    local actual
    actual=$("$PYTHON_BIN" -c \
        "from importlib.metadata import version; print(version('$package'))" \
        2>/dev/null)

    if [ "$actual" = "$expected" ]; then
        ok "$label ($actual)"
    else
        fail "$label version mismatch: expected $expected, found $actual"
    fi
}

if [ ! -f "$PYTHON_REQUIREMENTS" ]; then
    fail "Python requirements file not found"
else
    while IFS= read -r requirement || [ -n "$requirement" ]; do
        case "$requirement" in
            ""|\#*) continue ;;
        esac

        package="${requirement%%==*}"
        expected="${requirement#*==}"

        case "$package" in
            can-isotp)
                module="isotp"
                label="can-isotp"
                ;;
            python-can)
                module="can"
                label="python-can"
                ;;
            pwntools)
                module="pwn"
                label="Pwntools"
                ;;
            *)
                module="$package"
                label="$package"
                ;;
        esac

        python_check "$package" "$module" "$label" "$expected"
    done < "$PYTHON_REQUIREMENTS"
fi

echo
echo "=== Reverse Engineering ==="


for tool in file readelf objdump nm gdb gdb-multiarch qemu-aarch64; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool"
    else
        warn "$tool not found"
    fi
done

echo
echo "=== Firmware ==="

for tool in binwalk mksquashfs unsquashfs; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool"
    else
        warn "$tool not found"
    fi
done

echo
echo "=== CAN ==="

for tool in candump cansend; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool"
    else
        fail "$tool not found"
    fi
done

if ip link show vcan0 >/dev/null 2>&1; then
    ok "vcan0 exists"
else
    fail "vcan0 not found"
fi

if systemctl is-enabled vcan0.service >/dev/null 2>&1; then
    ok "vcan0.service enabled"
else
    warn "vcan0.service not enabled"
fi

if systemctl is-active vcan0.service >/dev/null 2>&1; then
    ok "vcan0.service active"
else
    fail "vcan0.service inactive"
fi

echo
echo "=== Vehicle Lab Tests ==="

UDS_LAB_DIR="$PROJECT_ROOT/labs/vehicle/uds-basic"
UDS_UNIT_TEST="$UDS_LAB_DIR/tests/test_ecu_unit.py"

if [ ! -f "$UDS_UNIT_TEST" ]; then
    warn "UDS unit test not found"
elif [ -z "$PYTHON_BIN" ]; then
    fail "UDS unit test skipped: no Python interpreter"
else
    # 离线单元测试：不依赖 vcan0，直接验证 ECU 的 UDS 分发逻辑
    if "$PYTHON_BIN" "$UDS_UNIT_TEST" >/dev/null 2>&1; then
        UNIT_COUNT=$("$PYTHON_BIN" "$UDS_UNIT_TEST" 2>&1 \
            | sed -n 's/^Ran \([0-9]\+\) tests\?.*/\1/p' | tail -1)
        ok "UDS unit tests passed (${UNIT_COUNT:-?} tests)"
    else
        fail "UDS unit tests failed (run: python $UDS_UNIT_TEST)"
    fi
fi

if [ -f "$UDS_LAB_DIR/tests/test_e2e_vcan.py" ]; then
    if ip link show vcan0 >/dev/null 2>&1; then
        echo "      e2e tests available: bash labs/vehicle/uds-basic/tests/run_tests.sh e2e"
    else
        warn "vcan0 missing, UDS e2e tests not runnable"
    fi
fi

echo
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

