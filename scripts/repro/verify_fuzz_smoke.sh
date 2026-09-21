#!/usr/bin/env bash
#
# verify_fuzz_smoke.sh —— CAN/ISO-TP fuzz 最小 smoke 可复现入口
#
# 完整 campaign（smoke30 / dim100 / dim500）属于实验，不进默认 CI；
# 本脚本只跑 3 个 case，验证四件事：
#   1. harness：isotp_fuzz.py 生成并执行 case（--smoke --max-cases 3）
#   2. case schema：case JSON 落盘、result.class 合法
#   3. 分类与 kernel oracle：三态（AVAILABLE/UNAVAILABLE/ERROR）如实
#      上报；oracle 非 AVAILABLE 时 kernel_events 必须为 null（≠0）
#   4. replay：replay.py --all 精确重放，退出码 0、issue(s)=0
#
# 依赖：vcan0 已存在且 UP（**独占**，勿与其它 CAN 测试并行）；
#       Python venv 含 python-can / can-isotp（默认 ~/.venvs/network，
#       可用 PYTHON 环境变量覆盖）。
# 产物全部写入 mktemp 临时目录，结束即删除；不提交生成物。
#
# 运行：bash scripts/repro/verify_fuzz_smoke.sh
# 退出码：0 = 全过；1 = 验证失败；2 = 环境不可用（vcan0/依赖缺失）。

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FUZZ_DIR="$ROOT/labs/vehicle/can-tools/fuzz"

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
OUT="$WORK/cases"

echo "workdir: $WORK"

# ---- 依赖检查（缺失 = 环境不可用，退出 2，不是验证失败）----
if [ -n "${PYTHON:-}" ] && [ -x "${PYTHON}" ]; then
    PYTHON_BIN="$PYTHON"
elif [ -x "$HOME/.venvs/network/bin/python" ]; then
    PYTHON_BIN="$HOME/.venvs/network/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "[MISS] Python 解释器未找到"
    exit 2
fi

if ! ip link show vcan0 >/dev/null 2>&1; then
    echo "[MISS] vcan0 不存在（先：sudo systemctl start vcan0.service）"
    exit 2
fi
if ! ip link show vcan0 | grep -q "UP"; then
    echo "[MISS] vcan0 存在但未 UP"
    exit 2
fi
ok "vcan0 UP（本脚本独占总线，请勿并发其它 CAN 测试）"

if ! "$PYTHON_BIN" -c "import can, isotp" >/dev/null 2>&1; then
    echo "[MISS] venv 缺少 python-can / can-isotp（安装 configs/environment/python-network-requirements.txt）"
    exit 2
fi
ok "Python venv：python-can / can-isotp 可导入（$PYTHON_BIN）"

# ---- 1. harness：生成并执行 3 个 case ----
"$PYTHON_BIN" "$FUZZ_DIR/isotp_fuzz.py" --smoke --max-cases 3 \
    --seed 12345 --out "$OUT" > "$WORK/fuzz.out" 2>&1
FRC=$?

if [ "$FRC" -eq 0 ]; then
    ok "fuzz harness 执行（rc=0）"
else
    fail "fuzz harness 执行失败 rc=$FRC"
    tail -5 "$WORK/fuzz.out"
fi

if grep -q "cases       = 3" "$WORK/fuzz.out"; then
    ok "case 生成数 = 3"
else
    fail "case 生成数不是 3"
    grep "cases" "$WORK/fuzz.out"
fi

# ---- 2. summary 与 kernel oracle 三态 ----
SUMMARY="$OUT/smoke-summary.json"

if [ ! -f "$SUMMARY" ]; then
    fail "smoke-summary.json 未落盘"
else
    ok "smoke-summary.json 落盘"

    "$PYTHON_BIN" - "$SUMMARY" "$OUT" <<'PY' > "$WORK/oracle_check.out" 2>&1
import json
import os
import sys

summary_path, out_dir = sys.argv[1], sys.argv[2]

s = json.load(open(summary_path, encoding="utf-8"))
ko = s.get("kernel_oracle")
assert isinstance(ko, dict), "summary 缺少 kernel_oracle（oracle 三态未如实上报）"
for k in ko:
    assert k in ("AVAILABLE", "UNAVAILABLE", "ERROR"), f"非法 oracle 状态 {k}"
assert sum(ko.values()) == s.get("total", 0), "oracle 计数与 total 不一致"

n_cases = 0
for name in sorted(os.listdir(out_dir)):
    if not name.endswith(".json") or name == "smoke-summary.json":
        continue
    n_cases += 1
    case = json.load(open(os.path.join(out_dir, name), encoding="utf-8"))
    res = case.get("result") or {}
    assert res.get("class"), f"{name}: result.class 缺失"
    oracle = res.get("kernel_oracle")
    assert oracle in ("AVAILABLE", "UNAVAILABLE", "ERROR"), \
        f"{name}: kernel_oracle 非法 {oracle!r}"
    if oracle != "AVAILABLE":
        # fail-closed 不变量：oracle 不可用 ≠ 0 kernel event
        assert res.get("kernel_events") is None, \
            f"{name}: oracle={oracle} 但 kernel_events 不是 null"
        assert res.get("kernel_delta_lines") is None, \
            f"{name}: oracle={oracle} 但 kernel_delta_lines 不是 null"

assert n_cases == 3, f"case JSON 数量 {n_cases} != 3"
print("oracle_summary_ok", ko, "cases_ok", n_cases)
PY
    ORC=$?
    if [ "$ORC" -eq 0 ]; then
        ok "schema/分类/oracle 三态校验通过：$(cat "$WORK/oracle_check.out")"
    else
        fail "schema/分类/oracle 三态校验失败"
        cat "$WORK/oracle_check.out"
    fi
fi

# ---- 3. replay 精确重放 ----
"$PYTHON_BIN" "$FUZZ_DIR/replay.py" "$OUT" --all > "$WORK/replay.out" 2>&1
RRC=$?

if [ "$RRC" -eq 0 ]; then
    ok "replay --all（rc=0）"
else
    fail "replay --all 失败 rc=$RRC"
    tail -5 "$WORK/replay.out"
fi

if grep -q "issue(s)           0" "$WORK/replay.out"; then
    ok "replay issue(s)=0（3 case 全部精确重放）"
else
    fail "replay issue(s) 不为 0"
    grep "issue(s)" "$WORK/replay.out"
fi

echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
