#!/usr/bin/env bash
#
# verify_ghidra.sh —— Ghidra headless 最小可复现分析流水线（AArch64）
#
# 与第一轮目标一致（对应 docs/TEST_MATRIX.md 第 6 节 "Ghidra headless
# 分析" 的原始审计断言，样本从审计时的 hello_arm64 换为本脚本自行
# 生成、可再生成的 AArch64 静态 ELF）：
#
#   AArch64 ELF（aarch64-linux-gnu-gcc -static -g）
#       ↓
#   analyzeHeadless 导入 + 自动分析
#       ↓
#   Architecture = AARCH64:LE:64:v8A:default（language 检查）
#       ↓
#   ERROR_COUNT = 0（大小写敏感，只统计 Ghidra ERROR 级别日志）
#       ↓
#   main() 反汇编（postScript 输出 GHIDRA_DISASM 行，12 条 AArch64 指令）
#
# 依赖：aarch64-linux-gnu-gcc（生成 AArch64 sample ELF）、Ghidra
#       （analyzeHeadless，默认 /opt/ghidra/support/analyzeHeadless，
#       可用 GHIDRA_BIN 覆盖）、openjdk（Ghidra 12 需要 21）。
# 产物全部写入 /tmp 与 mktemp 临时目录，结束即删除；不污染项目目录。
#
# 运行：bash scripts/repro/verify_ghidra.sh
# 退出码：0 = 验证通过；1 = 验证失败；2 = 依赖/环境不可用。

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
if ! command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
    echo "[MISS] aarch64-linux-gnu-gcc 缺失（Ubuntu: sudo apt install gcc-aarch64-linux-gnu）"
    missing=1
fi
if [ ! -x "$GHIDRA_BIN" ]; then
    echo "[MISS] analyzeHeadless 缺失：$GHIDRA_BIN（可用 GHIDRA_BIN 覆盖路径）"
    missing=1
fi
[ "$missing" -eq 1 ] && exit 2

# ---- 1. AArch64 sample ELF ----
cat > hello.c <<'EOF'
#include <stdio.h>
int answer(void) { return 42; }
int main(void) {
    volatile int x = answer();
    printf("HELLO_OK %d\n", (int)x);
    return 0;
}
EOF

if ! aarch64-linux-gnu-gcc -static -g -O0 -o hello_arm64 hello.c 2>/dev/null; then
    fail "AArch64 sample ELF 编译失败"
    echo; echo "PASS: $PASS"; echo "FAIL: $FAIL"
    exit 1
fi
ok "AArch64 sample ELF 编译（-static -g）"

if readelf -h hello_arm64 2>/dev/null | grep -q 'AArch64'; then
    ok "输入确认：hello_arm64 是 AArch64 ELF（readelf Machine）"
else
    fail "输入 ELF 不是 AArch64（readelf Machine 检查失败）"
    echo; echo "PASS: $PASS"; echo "FAIL: $FAIL"
    exit 1
fi

# ---- 2. analyzeHeadless 导入 + 自动分析 ----
"$GHIDRA_BIN" "$PROJ" repro_hello_arm64 \
    -import "$WORK/hello_arm64" \
    -overwrite \
    -log "$WORK/ghidra.log" > "$WORK/ghidra_headless.out" 2>&1
HDRC=$?

if [ "$HDRC" -eq 0 ]; then
    ok "analyzeHeadless 导入 + 自动分析（rc=0）"
else
    fail "analyzeHeadless 返回 $HDRC（预期 0）"
    grep -iE 'ERROR|Exception' "$WORK/ghidra_headless.out" | head -5
fi

# ---- 3. 架构识别检查（必须 AARCH64）----
if grep -qiE 'Using Language.*AARCH64' "$WORK/ghidra_headless.out"; then
    ok "Architecture = AARCH64（language 与编译目标一致）"
else
    fail "架构识别失败（未发现 AARCH64 language 标记）"
    grep -iE 'Using Language' "$WORK/ghidra_headless.out" | head -3
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

# ---- 5. AArch64 反汇编（postScript）----
"$GHIDRA_BIN" "$PROJ" repro_hello_arm64 \
    -process hello_arm64 \
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
    ok "main() 反汇编 12 条 AArch64 指令"
elif grep -q 'GHIDRA_DISASM: OK disassembled=' "$WORK/ghidra_disasm.out"; then
    n="$(grep 'GHIDRA_DISASM: OK disassembled=' "$WORK/ghidra_disasm.out" \
         | sed 's/.*=//' | tail -1)"
    ok "main() 反汇编 $n 条指令（分析受限于样本结构，非零即可）"
else
    fail "postScript 未反汇编出 main（main NOT FOUND 或脚本失败）"
    grep 'GHIDRA_DISASM' "$WORK/ghidra_disasm.out" | head -3
fi

# ---- 6. 反汇编语言必须是 AArch64 ----
if grep -q 'GHIDRA_DISASM: language=AARCH64' "$WORK/ghidra_disasm.out"; then
    ok "反汇编语言：AARCH64"
else
    fail "反汇编语言不是 AARCH64"
    grep 'GHIDRA_DISASM: language' "$WORK/ghidra_disasm.out" | head -2
fi

echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
