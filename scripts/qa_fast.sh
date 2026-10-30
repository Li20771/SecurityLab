#!/usr/bin/env bash
#
# SecurityLab —— 快速质量门禁（fast CI）
#
# 内容：lint(ruff) + 类型检查(mypy) + 纯离线单元测试。
#
# 特点：
#   - 不需要 vcan0 / SocketCAN，不需要 root，可在普通 CI runner 上运行；
#   - 离线测试通过各实验自带的 run_tests.sh 入口运行，每个测试文件在
#     **独立解释器进程**中执行（个别"模块纯净度"用例断言 sys.modules 不含
#     can/isotp，只有在独立进程下才成立，故不走 pytest 单会话收集）；
#   - 需要 vcan0 的端到端 / fuzz 测试不在此列，见 scripts/qa_extended.sh。
#
# 用法：  bash scripts/qa_fast.sh
# 退出码：0 = 全过；非 0 = 至少一项失败。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="$HOME/.venvs/network/bin"

if [ -x "$VENV_BIN/ruff" ]; then
    RUFF="$VENV_BIN/ruff"
else
    RUFF="$(command -v ruff || true)"
fi

if [ -x "$VENV_BIN/mypy" ]; then
    MYPY="$VENV_BIN/mypy"
else
    MYPY="$(command -v mypy || true)"
fi

if [ -x "$VENV_BIN/python" ]; then
    PYTHON_BIN="$VENV_BIN/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    PYTHON_BIN="$(command -v python || true)"
fi

FAIL=0

step() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

if [ -z "$RUFF" ] || [ -z "$MYPY" ] || [ -z "$PYTHON_BIN" ]; then
    echo "[FAIL] 未找到 ruff / mypy / python。请先安装到 venv："
    echo "       ~/.venvs/network/bin/pip install ruff mypy pytest"
    exit 2
fi

step "[1/5] ruff lint —— labs/vehicle/uds-basic + securitylab_mcp"
if "$RUFF" check "$ROOT/labs/vehicle/uds-basic" "$ROOT/securitylab_mcp"; then
    echo "[ OK ] ruff"
else
    echo "[FAIL] ruff"
    FAIL=1
fi

step "[2/5] mypy 类型检查 —— uds-basic/ecu + securitylab_mcp"
if "$MYPY" "$ROOT/labs/vehicle/uds-basic/ecu" "$ROOT/securitylab_mcp"; then
    echo "[ OK ] mypy"
else
    echo "[FAIL] mypy"
    FAIL=1
fi

step "[3/5] UDS 离线单元测试（协议层，无需 vcan）"
if bash "$ROOT/labs/vehicle/uds-basic/tests/run_tests.sh" unit; then
    echo "[ OK ] uds unit"
else
    echo "[FAIL] uds unit"
    FAIL=1
fi

step "[4/5] CAN tools 离线测试（解析 / fuzz harness，无需 ECU）"
if bash "$ROOT/labs/vehicle/can-tools/tests/run_tests.sh" offline; then
    echo "[ OK ] can-tools offline"
else
    echo "[FAIL] can-tools offline"
    FAIL=1
fi

step "[5/5] SecurityLab MCP 只读 server 离线测试（synthetic fixtures）"
if "$PYTHON_BIN" -m pytest -q -m "not vcan" "$ROOT/securitylab_mcp/tests"; then
    echo "[ OK ] mcp offline"
else
    echo "[FAIL] mcp offline"
    FAIL=1
fi

echo
echo "================================================================"
if [ "$FAIL" -eq 0 ]; then
    echo "  QA FAST RESULT: PASS"
else
    echo "  QA FAST RESULT: FAIL"
fi
echo "================================================================"
exit "$FAIL"
