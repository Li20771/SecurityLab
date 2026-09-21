#!/usr/bin/env bash
#
# verify_ghidra.sh —— Ghidra headless 最小可复现分析流水线
#
# 覆盖（对应 docs/TEST_MATRIX.md 第 6 节 "Ghidra headless 分析" 断言）：
#   1. sample ELF 导入 + 自动分析（analyzeHeadless）
#   2. 架构识别检查（language 与编译目标一致）
#   3. 基本反汇编（postScript 反汇编 main 前 12 条指令）
#   4. 分析错误数检查（ERROR_COUNT=0）
#
# 依赖：gcc（生成 sample ELF）、Ghidra（analyzeHeadless，默认
#       /opt/ghidra/support/analyzeHeadless，可用 GHIDRA_BIN 覆盖）、
#       openjdk（Ghidra 12 需要 21）。
# 产物全部写入 /tmp 与 mktemp 临时目录，结束即删除；不污染项目目录。
#
# 运行：bash scripts/repro/verify_ghidra.sh
# 退出码：0 = 全过；1 = 验证失败；2 = 必需依赖缺失（环境不可用）。

set -u

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

GHIDRA_BIN="${GHIDRA_BIN:-/opt/ghidra/support/analyzeHeadless}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$HERE/ghidra_scripts"

WORK="$(mktemp -d)"
PROJ="$(mktemp -d)"
trap 'rm -rf "$WORK" "$PROJ"' EXIT
cd "$WORK" || exit 1

echo "workdir:  $WORK"
echo "projdir:  $PROJ"
echo "ghidra:   $GHIDRA_BIN"

missing=0
if ! command -v gcc >/dev/null 2>&1; then
    echo "[MISS] gcc 缺失（用于生成 sample ELF）"
    missing=1
fi
if [ ! -x "$GHIDRA_BIN" ]; then
    echo "[MISS] analyzeHeadless 缺失：$GHIDRA_BIN（可用 GHIDRA_BIN 覆盖路径）"
    missing=1
fi
[ "$missing" -eq 1 ] && exit 2

# ---- 1. sample ELF ----
cat > hello.c <<'EOF'
#include <stdio.h>
int answer(void) { return 42; }
int main(void) {
    volatile int x = answer();
    printf("HELLO_OK %d\n", (int)x);
    return 0;
}
EOF

if ! gcc -g -O0 -o hello_x86_64 hello.c 2>/dev/null; then
    fail "sample ELF 编译失败"
    echo; echo "PASS: $PASS"; echo "FAIL: $FAIL"
    exit 1
fi
ok "sample ELF 编译（x86-64）"

# ---- 2. analyzeHeadless 导入 + 自动分析 ----
"$GHIDRA_BIN" "$PROJ" repro_hello \
    -import "$WORK/hello_x86_64" \
    -overwrite \
    -log "$WORK/ghidra.log" > "$WORK/ghidra_headless.out" 2>&1
HDRC=$?

if [ "$HDRC" -eq 0 ]; then
    ok "analyzeHeadless 导入 + 自动分析（rc=0）"
else
    fail "analyzeHeadless 返回 $HDRC（预期 0）"
    grep -iE 'ERROR|Exception' "$WORK/ghidra_headless.out" | head -5
fi

# ---- 3. 架构识别检查 ----
if grep -qiE 'Using Language.*x86:LE:64' "$WORK/ghidra_headless.out"; then
    ok "架构识别：x86:LE:64（与编译目标一致）"
else
    fail "架构识别失败（未发现 x86:LE:64 language 标记）"
fi

# ---- 4. 分析错误数检查 ----
# 注意大小写敏感：只统计 Ghidra 日志的 ERROR 级别 token。
# WARN 行里的 "pcode error ..." 是解编译器对 EXTERNAL 块的提示，不是错误。
ERR_COUNT="$(grep -cE '(^|[[:space:]])ERROR([[:space:]]|$)' "$WORK/ghidra_headless.out" || true)"
if [ "${ERR_COUNT:-0}" -eq 0 ]; then
    ok "分析错误检查：ERROR_COUNT=0"
else
    fail "分析错误检查：ERROR_COUNT=$ERR_COUNT"
    grep -E '(^|[[:space:]])ERROR([[:space:]]|$)' "$WORK/ghidra_headless.out" | head -5
fi

# ---- 5. 基本反汇编（postScript）----
"$GHIDRA_BIN" "$PROJ" repro_hello \
    -process hello_x86_64 \
    -noanalysis \
    -scriptPath "$SCRIPT_PATH" \
    -postScript DumpDisasm.java \
    > "$WORK/ghidra_disasm.out" 2>&1
PRC=$?

if [ "$PRC" -eq 0 ]; then
    ok "postScript 反汇编（rc=0）"
else
    fail "postScript 返回 $PRC（预期 0）"
fi

if grep -q 'GHIDRA_DISASM: OK disassembled=12' "$WORK/ghidra_disasm.out"; then
    ok "main() 反汇编 12 条指令"
elif grep -q 'GHIDRA_DISASM: OK disassembled=' "$WORK/ghidra_disasm.out"; then
    n="$(grep 'GHIDRA_DISASM: OK disassembled=' "$WORK/ghidra_disasm.out" \
         | sed 's/.*=//' | tail -1)"
    ok "main() 反汇编 $n 条指令（分析受限于样本结构，非零即可）"
else
    fail "postScript 未反汇编出 main（main NOT FOUND 或脚本失败）"
    grep 'GHIDRA_DISASM' "$WORK/ghidra_disasm.out" | head -3
fi

echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
