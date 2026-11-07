#!/usr/bin/env bash
# 在 WSL/Linux 中运行 OTA vcan0 端到端测试，输出保存到 report/pytest_vcan_output.txt。
# 前置：vcan0 已 UP；venv 有 isotp/udsoncan/pytest；gmssl 可 import。
# 本机回退：若 venv 缺 gmssl，用 PYTHONPATH 复用 Windows 侧纯 Python gmssl（无需安装）。
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-/home/lilike/.venvs/network/bin/python}"
cd "$HERE"

if ! "$PY" -c "import isotp, udsoncan, pytest" >/dev/null 2>&1; then
  echo "[!] $PY 缺少 isotp/udsoncan/pytest：请 pip install isotp udsoncan pytest gmssl"
  exit 2
fi

if ! "$PY" -c "import gmssl" >/dev/null 2>&1; then
  SITE="${GMSSL_SITE:-/mnt/c/Users/20771/AppData/Local/Python/pythoncore-3.14-64/Lib/site-packages}"
  export PYTHONPATH="$SITE${PYTHONPATH:+:$PYTHONPATH}"
fi

"$PY" -m pytest tests/test_ota_vcan.py -v --tb=short > "$HERE/report/pytest_vcan_output.txt" 2>&1
echo "EXIT=$?"
tail -n 30 "$HERE/report/pytest_vcan_output.txt"
