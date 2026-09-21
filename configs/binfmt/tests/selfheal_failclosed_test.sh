#!/usr/bin/env bash
#
# selfheal_failclosed_test.sh —— binfmt-selfheal v2.3 fail-closed 回归测试
#
# 验证工程收口要求的三态：
#
#   情况 A  helper 存在且成功  → 正常执行，quick 模式退出 0
#   情况 B  helper 缺失        → 明确报错、quick 模式退出 1，
#                               绝不调用 systemd-binfmt
#   情况 C  helper 执行失败    → 错误向上返回（quick 退出 1），
#                               绝不调用 systemd-binfmt
#
# 不需要 root：通过 BINFMT_REGISTRY / BINFMT_LOCK / BINFMT_HELPER /
# BINFMT_D_DIRS 测试接缝构造一个假的 binfmt_misc 注册表目录，
# 不触碰真实 /proc/sys/fs/binfmt_misc。
#
# 运行：bash configs/binfmt/tests/selfheal_failclosed_test.sh
# 退出码：0 = 全过；1 = 至少一项失败；2 = 环境无法运行（flock 缺失等）

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELFHEAL="$HERE/../binfmt-selfheal.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

if ! command -v flock >/dev/null 2>&1; then
    echo "[WARN] flock 不可用，无法运行本测试（请在 WSL Ubuntu 中运行）"
    exit 0
fi
if [ ! -f "$SELFHEAL" ]; then
    echo "selfheal 脚本不存在：$SELFHEAL" >&2
    exit 2
fi

# ----------------------------------------------------------------------
# 静态护栏：selfheal 源码中不得再出现 systemd-binfmt 的**执行调用**
# （注释叙述历史与报错文案提及该词是允许的；要防的是真实回退路径）
# ----------------------------------------------------------------------
if grep -qE '(^|[[:space:]])(exec[[:space:]]+)?/usr/lib/systemd/systemd-binfmt([[:space:]]|$)' \
       "$SELFHEAL"; then
    fail "源码级：selfheal 仍存在 systemd-binfmt 执行调用（flush 回退必须被移除）"
else
    ok "源码级：无 systemd-binfmt 执行调用（additive-only，无 flush 回退）"
fi

# ----------------------------------------------------------------------
# 假环境构造
# ----------------------------------------------------------------------
setup_case() {
    # $1 = case 名，$2 = helper 模式（ok | missing | failing）
    local name="$1" mode="$2"
    local d="$WORK/$name"

    mkdir -p "$d/registry" "$d/conf.d" "$d/bin"
    : > "$d/registry/status"
    : > "$d/registry/register"
    : > "$d/registry/WSLInterop"

    # 一条 binfmt.d 规则（自愈脚本只解析第一个 ':' 字段作为条目名）
    printf '%s\n' \
        ':qemu-aarch64:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/usr/libexec/qemu-binfmt/aarch64-binfmt-P:OCPF' \
        > "$d/conf.d/qemu-aarch64.conf"

    # logger stub：捕获全部日志（PATH 前置）
    cat > "$d/bin/logger" <<LOG
#!/bin/sh
echo "\$@" >> "$d/logger.log"
LOG
    chmod +x "$d/bin/logger"

    case "$mode" in
        ok)
            cat > "$d/bin/fake-helper" <<HELPER
#!/bin/sh
# 模拟 additive 注册成功：在假注册表中创建条目
touch "$d/registry/qemu-aarch64"
exit 0
HELPER
            chmod +x "$d/bin/fake-helper"
            ;;
        failing)
            cat > "$d/bin/fake-helper" <<HELPER
#!/bin/sh
# 模拟 helper 内部失败（如注册表写入 EIO）
exit 1
HELPER
            chmod +x "$d/bin/fake-helper"
            ;;
        missing)
            ;; # 不创建 helper
    esac
}

run_quick() {
    # $1 = case 名，$2 = helper 模式；输出该 case 的退出码
    local name="$1" mode="$2"
    local d="$WORK/$name"
    local helper="$d/bin/fake-helper"
    [ "$mode" = "missing" ] && helper="$d/bin/does-not-exist"

    BINFMT_REGISTRY="$d/registry" \
    BINFMT_LOCK="$d/lock" \
    BINFMT_HELPER="$helper" \
    BINFMT_D_DIRS="$d/conf.d" \
    PATH="$d/bin:/usr/bin:/bin" \
        bash "$SELFHEAL" quick >/dev/null 2>&1
}

# ----------------------------------------------------------------------
# 情况 A：helper 存在且成功 → 正常执行
# ----------------------------------------------------------------------
setup_case case_a ok
rc_a="$(run_quick case_a ok; echo $?)"

if [ "$rc_a" -eq 0 ]; then
    ok "情况 A：helper 存在且成功 → quick 退出 0"
else
    fail "情况 A：预期退出 0，实际 $rc_a"
fi

if [ -e "$WORK/case_a/registry/qemu-aarch64" ]; then
    ok "情况 A：缺失条目被注册（fake registry 出现 qemu-aarch64）"
else
    fail "情况 A：fake registry 未出现 qemu-aarch64"
fi

if grep -q "fail-closed" "$WORK/case_a/logger.log" 2>/dev/null; then
    fail "情况 A：正常路径不应出现 fail-closed 报错"
else
    ok "情况 A：无 fail-closed 误报"
fi

# ----------------------------------------------------------------------
# 情况 B：helper 缺失 → 明确报错 + 非零 + 不调用 systemd-binfmt
# ----------------------------------------------------------------------
setup_case case_b missing
rc_b="$(run_quick case_b missing; echo $?)"

if [ "$rc_b" -ne 0 ]; then
    ok "情况 B：helper 缺失 → quick 退出 $rc_b（非零）"
else
    fail "情况 B：helper 缺失应退出非零，实际 0"
fi

if grep -q "missing" "$WORK/case_b/logger.log" 2>/dev/null \
   && grep -q "fail-closed" "$WORK/case_b/logger.log" 2>/dev/null; then
    ok "情况 B：日志明确报错（missing + fail-closed）"
else
    fail "情况 B：日志缺少明确报错（missing / fail-closed）"
fi

if [ -e "$WORK/case_b/registry/qemu-aarch64" ]; then
    fail "情况 B：helper 缺失不应注册条目"
else
    ok "情况 B：未注册条目（未伪装成功）"
fi

if [ -s "$WORK/case_b/registry/register" ]; then
    fail "情况 B：register 文件被写入（可能发生了 flush/回退）"
else
    ok "情况 B：register 未被写入（无 systemd-binfmt 回退痕迹）"
fi

# ----------------------------------------------------------------------
# 情况 C：helper 执行失败 → 错误向上返回
# ----------------------------------------------------------------------
setup_case case_c failing
rc_c="$(run_quick case_c failing; echo $?)"

if [ "$rc_c" -ne 0 ]; then
    ok "情况 C：helper 执行失败 → quick 退出 $rc_c（错误向上返回）"
else
    fail "情况 C：helper 失败应退出非零，实际 0"
fi

if grep -q "failed" "$WORK/case_c/logger.log" 2>/dev/null \
   && grep -q "fail-closed" "$WORK/case_c/logger.log" 2>/dev/null; then
    ok "情况 C：日志明确报错（failed + fail-closed）"
else
    fail "情况 C：日志缺少明确报错（failed / fail-closed）"
fi

if [ -s "$WORK/case_c/registry/register" ]; then
    fail "情况 C：register 文件被写入（可能发生了 flush/回退）"
else
    ok "情况 C：register 未被写入（无 systemd-binfmt 回退痕迹）"
fi

# ----------------------------------------------------------------------
echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
