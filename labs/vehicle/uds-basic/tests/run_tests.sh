#!/usr/bin/env bash
#
# UDS Basic 实验测试入口
#
# 运行两类测试：
#
#   1. 离线单元测试  test_ecu_unit.py
#      直接调用 handle_uds_request()，不需要 vcan0、不需要 root
#
#   2. vcan0 端到端测试  test_e2e_vcan.py
#      通过真实 ISO-TP / SocketCAN 链路与虚拟 ECU 子进程通信
#      以及 test_isotp_options_vcan.py：ISO-TP 传输参数（padding /
#      stmin / blocksize / timeout）的在网实验矩阵
#      以及 test_gateway_runtime_vcan.py（OQ7）：gateway_runtime.py 守护进程
#      的物理/功能寻址、总线静默、状态隔离、畸形请求与生命周期（SIGTERM）
#
# 用法：
#
#   bash tests/run_tests.sh            # 运行全部测试
#   bash tests/run_tests.sh unit       # 只运行离线单元测试
#   bash tests/run_tests.sh e2e        # 只运行 vcan0 端到端测试
#
# 环境变量：
#
#   PYTHON   指定 Python 解释器
#
# 安全边界：
#
#   只使用 vcan0（虚拟 CAN），不接触真实车辆 / 真实 ECU / 真实 CAN 适配器。

set -u

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTS_DIR="$LAB_DIR/tests"

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

# ----------------------------------------------------------------------
# Python 解释器
# ----------------------------------------------------------------------

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
echo " UDS Basic Lab Test Runner"
echo "========================================"
echo "Lab:    $LAB_DIR"
echo "Python: $PYTHON_BIN"
echo "Mode:   $MODE"
echo

# ----------------------------------------------------------------------
# 离线单元测试
# ----------------------------------------------------------------------

run_unit() {
    echo "=== Unit tests (offline, no CAN) ==="
    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_ecu_unit.py"; then
        ok "ECU unit tests passed"
    else
        fail "ECU unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_keygen_unit.py"; then
        ok "keygen unit tests passed"
    else
        fail "keygen unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_services_extra_unit.py"; then
        ok "0x2E/0x3E extra services unit tests passed"
    else
        fail "extra services unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_gateway_unit.py"; then
        ok "multi-ECU gateway routing unit tests passed"
    else
        fail "multi-ECU gateway routing unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_gateway_runtime_unit.py"; then
        ok "gateway runtime (dispatch/lifecycle/error containment) unit tests passed"
    else
        fail "gateway runtime unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_contract_pins.py"; then
        ok "C1 authorization / NRC contract pins passed"
    else
        fail "C1 contract pins failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_service_spec_unit.py"; then
        ok "C1 service specification model + conformance tests passed"
    else
        fail "C1 service specification tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_phase2_framework_unit.py"; then
        ok "C1 phase 2 execution framework unit tests passed"
    else
        fail "C1 phase 2 framework tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_phase2_a0_golden.py"; then
        ok "C1 phase 2 A0 legacy golden corpus replay passed"
    else
        fail "C1 phase 2 A0 golden replay failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_phase2_b0_0x2e_golden.py"; then
        ok "C1 phase 2 B0 0x2E legacy golden corpus structure tests passed"
    else
        fail "C1 phase 2 B0 0x2E golden tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_phase2_b2_0x2e_golden.py"; then
        ok "C1 phase 2 B2 0x2E legacy oracle replay equivalence passed"
    else
        fail "C1 phase 2 B2 0x2E golden replay failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c1_phase2_b4_inline_checks.py"; then
        ok "C1 phase 2 B4 inline defensive-check audit passed"
    else
        fail "C1 phase 2 B4 inline-check audit failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c2_a0_gateway_golden.py"; then
        ok "C2 A0 legacy gateway golden corpus structure tests passed"
    else
        fail "C2 A0 gateway golden tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c2_a1_empty_policy_trace.py"; then
        ok "C2 A1 empty policy + policy trace mechanism tests passed"
    else
        fail "C2 A1 empty policy / trace tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c2_a2_legacy_current_differential.py"; then
        ok "C2 A2 legacy / current differential audit passed"
    else
        fail "C2 A2 legacy / current differential audit failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c2_a4_override_bypass.py"; then
        ok "C2 A4 override / bypass invariant audit passed"
    else
        fail "C2 A4 override / bypass audit failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c3_a0_baseline.py"; then
        ok "C3 A0 security access baseline (pre-implementation) passed"
    else
        fail "C3 A0 security access baseline failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c3_a1_gateway_security_access.py"; then
        ok "C3 A1 gateway security access contract passed"
    else
        fail "C3 A1 gateway security access contract failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_c3_a2_direct_gateway_differential.py"; then
        ok "C3 A2 direct ECU / gateway differential passed"
    else
        fail "C3 A2 direct / gateway differential failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_dtc_unit.py"; then
        ok "0x19 ReadDTCInformation unit tests passed"
    else
        fail "ReadDTCInformation unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_dtc_clear_unit.py"; then
        ok "0x14 ClearDiagnosticInformation unit tests passed"
    else
        fail "ClearDiagnosticInformation unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_routine_unit.py"; then
        ok "0x31 RoutineControl unit tests passed"
    else
        fail "RoutineControl unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_transfer_unit.py"; then
        ok "0x34/0x36/0x37 download transfer unit tests passed"
    else
        fail "download transfer unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_upload_unit.py"; then
        ok "0x35 RequestUpload unit tests passed"
    else
        fail "RequestUpload unit tests failed"
    fi

    echo

    if "$PYTHON_BIN" "$TESTS_DIR/test_file_transfer_unit.py"; then
        ok "0x38 RequestFileTransfer unit tests passed"
    else
        fail "RequestFileTransfer unit tests failed"
    fi

    echo
}

# ----------------------------------------------------------------------
# vcan0 端到端测试
# ----------------------------------------------------------------------

run_e2e() {
    echo "=== End-to-end tests ($VCAN_INTERFACE) ==="
    echo

    if ! ip link show "$VCAN_INTERFACE" >/dev/null 2>&1; then
        warn "$VCAN_INTERFACE 不存在，跳过端到端测试"
        warn "请先启动 vcan0：sudo systemctl start vcan0.service"
        echo
        return
    fi

    if ! ip link show "$VCAN_INTERFACE" | grep -q "UP"; then
        warn "$VCAN_INTERFACE 存在但未 UP，跳过端到端测试"
        echo
        return
    fi

    ok "$VCAN_INTERFACE 可用"

    if "$PYTHON_BIN" "$TESTS_DIR/test_e2e_vcan.py"; then
        ok "end-to-end tests passed"
    else
        fail "end-to-end tests failed"
    fi

    echo

    if [ -f "$TESTS_DIR/test_isotp_options_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_isotp_options_vcan.py"; then
            ok "ISO-TP transport option tests passed"
        else
            fail "ISO-TP transport option tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_write_did_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_write_did_vcan.py"; then
            ok "0x2E/0x3E write-DID / TesterPresent e2e tests passed"
        else
            fail "write-DID / TesterPresent e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_multi_ecu_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_multi_ecu_vcan.py"; then
            ok "multi-ECU physical addressing e2e tests passed"
        else
            fail "multi-ECU physical addressing e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_gateway_runtime_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_gateway_runtime_vcan.py"; then
            ok "gateway runtime daemon e2e tests passed"
        else
            fail "gateway runtime daemon e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_dtc_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_dtc_vcan.py"; then
            ok "0x19 ReadDTCInformation e2e tests passed"
        else
            fail "ReadDTCInformation e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_dtc_clear_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_dtc_clear_vcan.py"; then
            ok "0x14 ClearDiagnosticInformation e2e tests passed"
        else
            fail "ClearDiagnosticInformation e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_routine_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_routine_vcan.py"; then
            ok "0x31 RoutineControl e2e tests passed"
        else
            fail "RoutineControl e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_transfer_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_transfer_vcan.py"; then
            ok "0x34/0x36/0x37 download transfer e2e tests passed"
        else
            fail "download transfer e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_upload_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_upload_vcan.py"; then
            ok "0x35 RequestUpload e2e tests passed"
        else
            fail "RequestUpload e2e tests failed"
        fi

        echo
    fi

    if [ -f "$TESTS_DIR/test_file_transfer_vcan.py" ]; then
        if "$PYTHON_BIN" "$TESTS_DIR/test_file_transfer_vcan.py"; then
            ok "0x38 RequestFileTransfer e2e tests passed"
        else
            fail "RequestFileTransfer e2e tests failed"
        fi

        echo
    fi
}

# ----------------------------------------------------------------------

case "$MODE" in
    unit)
        run_unit
        ;;
    e2e)
        run_e2e
        ;;
    all)
        run_unit
        run_e2e
        ;;
    *)
        echo "未知模式：$MODE（可用：all | unit | e2e）" >&2
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
