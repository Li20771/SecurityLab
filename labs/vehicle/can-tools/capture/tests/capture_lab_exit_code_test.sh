#!/usr/bin/env bash
#
# capture_lab_exit_code_test.sh —— capture_lab.sh 退出码传播回归测试
#
# 历史缺陷（OQ-2d）：
#
#   capture_lab.sh 汇总段的三个结果只印到屏幕，脚本**恒以 0 退出**：
#
#       if [ "$PASS_A" = PASS ] && ... ; then
#         echo "CAPTURE_LAB=PASS"
#       else
#         echo "CAPTURE_LAB=CHECK"     # ← 只打印，没有 exit 1
#       fi
#
#   而调用方 scripts/qa_extended.sh:95 是按**退出码**判定的：
#
#       if bash .../capture_lab.sh; then echo "[ OK ] capture lab"
#       else echo "[FAIL] capture lab"; FAIL=1; fi
#
#   ⇒ 段失败 + "CAPTURE_LAB=CHECK" 会被上报成 `[ OK ] capture lab`。
#     一次真实失败被汇总成通过（fail-open）。
#
# 本测试用 stub 工具模拟"单个子项失败"，直接验证修复后的语义：
#
#   场景 1  三段全失败（无 vcan0 / 工具缺失）
#           → 必须**非 0 退出**，且**不得**出现 `[ OK ]` 语义的假通过
#   场景 2  找不到任何 Python 解释器
#           → 必须 exit 2（环境无法运行），而不是继续跑出假结果
#   场景 3  即使 CAPTURE_LAB=CHECK，退出码也必须非 0（核心不变式）
#
# 安全性：只使用 stub 工具与临时目录；不接触真实 CAN / vcan0。
#
# 运行（WSL Ubuntu）：bash labs/vehicle/can-tools/capture/tests/capture_lab_exit_code_test.sh
# 退出码：0 = 全过；1 = 至少一项失败；2 = 环境无法运行。

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAPTURE_LAB="$HERE/../capture_lab.sh"

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

if [ ! -f "$CAPTURE_LAB" ]; then
    echo "capture_lab.sh 不存在：$CAPTURE_LAB" >&2
    exit 2
fi
if ! command -v bash >/dev/null 2>&1; then
    echo "需要 bash" >&2
    exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ----------------------------------------------------------------------
# 场景 1：无 vcan0 / 工具缺失 ⇒ 必须有非 0 退出码
# ----------------------------------------------------------------------
# 用一个**空**的 stub bin 目录 + 极简 PATH，使 candump/cansend/tshark 均不可见。
# 保留 bash / python3 / 必要 coreutils，否则脚本会因完全无法运行而 exit 2，
# 那就测不到"段失败 ⇒ 退出 1"这条路径。
STUB_BIN="$WORK/stub-bin"
mkdir -p "$STUB_BIN"

# 只放行脚本真正依赖的 coreutils + 解释器，其余（candump/cansend/tshark/
# canplayer）故意缺席，以制造"子项失败"。
for tool in bash sh awk sed grep tr sort diff wc head cat tee kill sleep \
            mkdir rm printf env mktemp tail ls dirname pwd; do
    real="$(command -v "$tool" 2>/dev/null || true)"
    [ -n "$real" ] && ln -sf "$real" "$STUB_BIN/$tool"
done

PYTHON_REAL="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [ -n "$PYTHON_REAL" ]; then
    ln -sf "$PYTHON_REAL" "$STUB_BIN/python3"
fi

echo "=== 场景 1: 子项失败必须产生非 0 退出码 ==="
OUT1="$WORK/run1.out"
set +e
env -i PATH="$STUB_BIN" HOME="$WORK" bash "$CAPTURE_LAB" > "$OUT1" 2>&1
RC1=$?
set -e 2>/dev/null || true

echo "capture_lab.sh 退出码 = $RC1"
echo "---- 输出尾部 ----"
tail -6 "$OUT1"
echo "-----------------"

if [ "$RC1" -ne 0 ]; then
    ok "子项失败时退出码非 0（rc=$RC1）"
else
    fail "子项失败时仍以 0 退出 —— fail-open 未修复"
fi

# 核心不变式：只要出现 CAPTURE_LAB=CHECK，退出码就必须非 0。
if grep -q 'CAPTURE_LAB=CHECK' "$OUT1"; then
    if [ "$RC1" -ne 0 ]; then
        ok "CAPTURE_LAB=CHECK 与退出码一致（非 0）"
    else
        fail "打印了 CAPTURE_LAB=CHECK 却以 0 退出 —— 正是历史缺陷"
    fi
else
    # 未走到汇总段（例如提前失败）也可接受，但退出码仍须非 0
    ok "未产生 CAPTURE_LAB 汇总（提前失败），退出码非 0 仍成立"
fi

# ----------------------------------------------------------------------
# 场景 2：找不到 Python 解释器 ⇒ 必须 exit 2（环境无法运行）
# ----------------------------------------------------------------------
echo
echo "=== 场景 2: 无 Python 解释器必须 fail-closed (exit 2) ==="
# 构造一个连 python 都没有的 PATH
STUB_NOPY="$WORK/stub-nopy"
mkdir -p "$STUB_NOPY"
for tool in bash sh awk sed grep tr sort diff wc head cat tee kill sleep \
            mkdir rm printf env mktemp tail ls dirname pwd; do
    real="$(command -v "$tool" 2>/dev/null || true)"
    [ -n "$real" ] && ln -sf "$real" "$STUB_NOPY/$tool"
done

OUT2="$WORK/run2.out"
set +e
env -i PATH="$STUB_NOPY" HOME="$WORK" PYTHON= bash "$CAPTURE_LAB" > "$OUT2" 2>&1
RC2=$?
set -e 2>/dev/null || true

echo "capture_lab.sh 退出码 = $RC2"
tail -3 "$OUT2"

if [ "$RC2" -eq 2 ]; then
    ok "无解释器时 exit 2（区分于段失败的 exit 1）"
else
    fail "无解释器时退出码为 $RC2，期望 2"
fi

# ----------------------------------------------------------------------
# 场景 3：静态不变式 —— 汇总段之后必须存在 exit
# ----------------------------------------------------------------------
echo
echo "=== 场景 3: 汇总段必须具备显式退出码 ==="
if grep -q 'CAPTURE_LAB=CHECK' "$CAPTURE_LAB"; then
    # CHECK 分支之后必须紧跟一个非 0 的 exit
    if awk '/CAPTURE_LAB=CHECK/{found=1} found && /exit [1-9]/{ok=1} END{exit !ok}' "$CAPTURE_LAB"; then
        ok "CAPTURE_LAB=CHECK 之后存在非 0 exit"
    else
        fail "CAPTURE_LAB=CHECK 之后没有非 0 exit"
    fi
else
    fail "脚本中找不到 CAPTURE_LAB=CHECK 汇总标记"
fi

# ----------------------------------------------------------------------
echo
echo "========================================"
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
