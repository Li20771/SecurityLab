#!/usr/bin/env bash
#
# verify_qemu_gdb.sh —— QEMU 用户态 -g 与 gdb-multiarch 远程调试最小闭环
#
# 覆盖（对应 docs/TEST_MATRIX.md 第 6 节 "QEMU+GDB 远程" 断言）：
#   1. qemu-aarch64 -g <port> 挂起等待 gdb stub
#   2. gdb-multiarch target remote 连接
#   3. breakpoint（main）命中
#   4. continue 后程序正常退出（qemu rc=0）
#
# 依赖：aarch64-linux-gnu-gcc、qemu-aarch64、gdb-multiarch。
# 产物全部写入 mktemp 临时目录，结束即删除；不污染系统。
#
# 运行：bash scripts/repro/verify_qemu_gdb.sh
# 环境变量：REPRO_GDB_PORT  调试端口（默认 12347）
# 退出码：0 = 全过；1 = 验证失败；2 = 必需依赖缺失（环境不可用）。

set -u

PASS=0
FAIL=0

ok()   { echo "[ OK ] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

PORT="${REPRO_GDB_PORT:-12347}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 1

echo "workdir: $WORK  port: $PORT"

missing=0
for t in aarch64-linux-gnu-gcc qemu-aarch64 gdb-multiarch; do
    if ! command -v "$t" >/dev/null 2>&1; then
        echo "[MISS] $t 缺失（Ubuntu: sudo apt install gcc-aarch64-linux-gnu qemu-user gdb-multiarch）"
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

if ! aarch64-linux-gnu-gcc -static -g -O0 -o hello_arm64 hello.c 2>/dev/null; then
    fail "AArch64 编译失败"
    echo; echo "PASS: $PASS"; echo "FAIL: $FAIL"
    exit 1
fi
ok "AArch64 编译（-static -g）"

# ---- qemu -g 挂起 + gdb remote 断点闭环 ----
qemu-aarch64 -g "$PORT" ./hello_arm64 >/dev/null 2>&1 &
QPID=$!

# 等 qemu 进入 gdb stub 等待状态（进程还活着即代表在等 gdb 连接）
sleep 1.5
if ! kill -0 "$QPID" 2>/dev/null; then
    fail "qemu-aarch64 -g 未进入 gdb stub 等待（进程已退出，端口 $PORT 可能被占用）"
    wait "$QPID" 2>/dev/null
    echo; echo "PASS: $PASS"; echo "FAIL: $FAIL"
    exit 1
fi
ok "qemu-aarch64 -g $PORT 挂起等待 gdb（pid=$QPID）"

GDB_OUT="$(gdb-multiarch -q -batch \
    -ex 'file ./hello_arm64' \
    -ex 'set architecture aarch64' \
    -ex "target remote localhost:$PORT" \
    -ex 'break main' \
    -ex 'continue' \
    -ex 'printf "GDB_BREAK_HIT pc=0x%lx\n", (long)$pc' \
    -ex 'continue' 2>&1)"

wait "$QPID"
QRC=$?

if printf '%s\n' "$GDB_OUT" | grep -q 'GDB_BREAK_HIT pc=0x'; then
    ok "gdb-multiarch remote 连接并命中 main 断点"
else
    fail "gdb 未命中 main 断点（remote 连接或 breakpoint 失败）"
    printf '%s\n' "$GDB_OUT" | head -5
fi

if [ "$QRC" -eq 0 ]; then
    ok "continue 后程序正常退出（qemu rc=0）"
else
    fail "continue 后 qemu 退出码 $QRC（预期 0）"
fi

echo
echo "PASS: $PASS"
echo "FAIL: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
