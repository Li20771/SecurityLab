#!/usr/bin/env bash
#
# verify_arm.sh —— ARM/AArch64 编译与执行最小可复现验证
#
# 覆盖（对应 docs/TEST_MATRIX.md 第 6 节的能力断言）：
#   1. AArch64 静态编译（aarch64-linux-gnu-gcc -static -g）
#   2. AArch64 执行（binfmt 透明执行优先；不可用时回退 qemu-aarch64）
#   3. armhf 静态编译（arm-linux-gnueabihf-gcc -static -g）
#   4. armhf 执行（qemu-arm 显式执行）
#
# 依赖：aarch64-linux-gnu-gcc、arm-linux-gnueabihf-gcc、qemu-arm；
#       qemu-aarch64 仅在 binfmt 透明执行不可用时才需要。
# 产物全部写入 mktemp 临时目录，脚本结束即删除；不污染系统、不提交生成物。
#
# 运行：bash scripts/repro/verify_arm.sh
# 退出码：0 = 全过；1 = 至少一项验证失败；2 = 必需依赖缺失（环境不可用）。

set -u

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 1

echo "workdir: $WORK"

missing=0
for t in aarch64-linux-gnu-gcc arm-linux-gnueabihf-gcc qemu-arm; do
    if ! command -v "$t" >/dev/null 2>&1; then
        echo "[MISS] $t 缺失（Ubuntu: sudo apt install gcc-aarch64-linux-gnu gcc-arm-linux-gnueabihf qemu-user）"
        missing=1
    fi
done
[ "$missing" -eq 1 ] && exit 2

cat > hello.c <<'EOF'
#include <stdio.h>
int answer(void) { return 42; }
int main(void) {
    volatile int x = answer();
    printf("ARM_RUNTIME_OK %d\n", (int)x);
    return 0;
}
EOF

# ---- 1. AArch64 编译 ----
if aarch64-linux-gnu-gcc -static -g -O0 -o hello_arm64 hello.c 2>/dev/null; then
    ok "AArch64 静态编译"
else
    fail "AArch64 静态编译失败"
fi

# ---- 2. armhf 编译 ----
if arm-linux-gnueabihf-gcc -static -g -O0 -o hello_armhf hello.c 2>/dev/null; then
    ok "armhf 静态编译"
else
    fail "armhf 静态编译失败"
fi

[ -x "$WORK/hello_arm64" ] || { echo; echo "PASS: $PASS"; echo "FAIL: $FAIL"; exit 1; }

# 架构自查：文件确实是 AArch64 / ARM ELF
if readelf -h hello_arm64 2>/dev/null | grep -q 'AArch64'; then
    ok "hello_arm64 是 AArch64 ELF（readelf Machine）"
else
    fail "hello_arm64 架构检查失败"
fi

if readelf -h hello_armhf 2>/dev/null | grep -q 'ARM'; then
    ok "hello_armhf 是 ARM ELF（readelf Machine）"
else
    fail "hello_armhf 架构检查失败"
fi

# ---- 3. AArch64 执行：binfmt 透明执行优先，qemu-aarch64 回退 ----
OUT64=""
if OUT64=$(./hello_arm64 2>/dev/null); then
    :
else
    if command -v qemu-aarch64 >/dev/null 2>&1; then
        OUT64=$(qemu-aarch64 ./hello_arm64 2>/dev/null)
    fi
fi

if [ "$OUT64" = "ARM_RUNTIME_OK 42" ]; then
    ok "AArch64 执行（输出 ARM_RUNTIME_OK 42）"
else
    fail "AArch64 执行失败（binfmt 与 qemu-aarch64 均未得到预期输出）"
fi

# ---- 4. armhf 执行：qemu-arm 显式 ----
OUTHF=""
OUTHF=$(qemu-arm ./hello_armhf 2>/dev/null)

if [ "$OUTHF" = "ARM_RUNTIME_OK 42" ]; then
    ok "armhf qemu-arm 执行（输出 ARM_RUNTIME_OK 42）"
else
    fail "armhf qemu-arm 执行失败"
fi

echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
