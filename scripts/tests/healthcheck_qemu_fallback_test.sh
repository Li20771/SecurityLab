#!/usr/bin/env bash
#
# healthcheck_qemu_fallback_test.sh —— healthcheck ARM64 qemu fallback 回归测试
#
# 历史缺陷（工程收口 2026-09-21 修复）：
#
#     elif command -v qemu-aarch64 >/dev/null 2>&1 \
#          && qemu-aarch64 "$ARM_TMP/t" >/dev/null 2>&1 \
#          && [ "$?" = "42" ]; then
#
# 这条 `&&` 链要求 qemu-aarch64 退出 0 才继续判断 `$?`——但 qemu-user
# **透传 guest 退出码**（guest return 42 → qemu 退出 42，非零），
# 于是 `&&` 短路、条件恒为假：binfmt 透明执行不可用时 fallback 永远
# 不触发，明明 qemu 能执行却误报 FAIL。
#
# 本测试通过 PATH 前置 stub 工具验证修复后的语义：
#
#   场景 1  透明执行 rc=1（模拟 binfmt 不可用），qemu stub 退出 42
#           → 必须报告 "ARM64 compile + qemu-aarch64 exec (exit 42)" PASS
#   场景 2  qemu stub 退出 0（模拟 qemu 也无法执行 guest）
#           → 必须 FAIL 且报出两个 rc
#
# 不修改任何真实工具链；stub 只影响 healthcheck 的 ARM64 检查段。
# 运行（WSL Ubuntu）：bash scripts/tests/healthcheck_qemu_fallback_test.sh
# 退出码：0 = 全过；1 = 至少一项失败；2 = 环境无法运行。

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTHCHECK="$HERE/../healthcheck.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

if [ ! -f "$HEALTHCHECK" ]; then
    echo "healthcheck.sh 不存在：$HEALTHCHECK" >&2
    exit 2
fi
if ! command -v bash >/dev/null 2>&1 || ! command -v grep >/dev/null 2>&1; then
    echo "需要 bash / grep" >&2
    exit 2
fi

# stub gcc：不真正编译，把输出 "二进制" 写成 /bin/false 的副本
# （healthcheck 后续直接执行它 → 退出 1，模拟 binfmt 透明执行失败）。
# gcc 调用形如：aarch64-linux-gnu-gcc -static t.c -o t  →  输出路径是 $4。
make_stubs() {
    local dir="$1" qemu_rc="$2"

    mkdir -p "$dir"
    cat > "$dir/aarch64-linux-gnu-gcc" <<'EOF'
#!/bin/sh
cp /bin/false "$4" 2>/dev/null || exit 9
exit 0
EOF
    cat > "$dir/qemu-aarch64" <<EOF
#!/bin/sh
exit $qemu_rc
EOF
    chmod +x "$dir/aarch64-linux-gnu-gcc" "$dir/qemu-aarch64"
}

run_healthcheck() {
    # 前置 stub，其余 PATH 保留真实系统目录
    PATH="$1:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        bash "$HEALTHCHECK" 2>&1
}

# ----------------------------------------------------------------------
# 场景 1：qemu stub 透传 42 → fallback 必须命中
# ----------------------------------------------------------------------
make_stubs "$WORK/stub42" 42
out1="$(run_healthcheck "$WORK/stub42")"

if printf '%s\n' "$out1" | grep -q '^\[PASS\] ARM64 compile + qemu-aarch64 exec (exit 42)$'; then
    ok "场景 1：qemu exit 42 → fallback 命中并报 PASS"
else
    fail "场景 1：未看到 PASS（qemu-aarch64 exec），说明 fallback 仍被短路"
fi

# ----------------------------------------------------------------------
# 场景 2：qemu stub 退出 0 → 必须 FAIL 且报出两个 rc
# ----------------------------------------------------------------------
make_stubs "$WORK/stub0" 0
out2="$(run_healthcheck "$WORK/stub0")"

if printf '%s\n' "$out2" | grep -qE '^\[FAIL\] ARM64 binary built but neither binfmt nor qemu-aarch64 executed it \(transparent rc=1, qemu rc=0\)$'; then
    ok "场景 2：qemu exit 0 → 明确 FAIL（transparent rc=1, qemu rc=0）"
else
    fail "场景 2：FAIL 信息缺失或 rc 未如实上报"
fi

# ----------------------------------------------------------------------
# 场景 3：透明执行成功路径不受影响（真实工具链可用时）
# 由 Level 4 真实环境 healthcheck 运行覆盖；此处仅确认脚本结构上
# 透明执行优先于 fallback（静态断言：先判断 RC=42，再进入 qemu 分支）。
# ----------------------------------------------------------------------
if grep -q 'if \[ "\$RC" -eq 42 \]; then' "$HEALTHCHECK"; then
    ok "场景 3（静态）：透明执行（rc=42）优先于 qemu fallback 判定"
else
    fail "场景 3（静态）：未找到透明执行优先判定结构"
fi

echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
