#!/usr/bin/env bash
#
# Instrument the synthetic target with AFL++.
#
# We use the GCC-based afl-gcc / afl-g++ wrappers rather than afl-clang-fast:
# the distro afl-clang-fast LLVM pass is built against LLVM 17 while this
# system's default clang is 18, and the pass .so fails to load under clang-18
# (undefined LLVM symbols). afl-gcc has no LLVM version coupling and uses
# GCC's own libasan for the ASan build. Both instrumented binaries are proven
# to build and run here.
#
# Usage: bash build.sh
set -eu
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-/tmp}/afl-demo"
mkdir -p "$WORK"
cp "$LAB_DIR/vuln.c" "$WORK/vuln.c"
cd "$WORK"

command -v afl-gcc >/dev/null || { echo "afl++ not installed (apt: afl++)" >&2; exit 2; }

echo "[*] ASan instrumented build (precise crash triage)"
AFL_USE_ASAN=1 afl-gcc -O1 -g -o vuln_asan vuln.c

echo "[*] Plain instrumented build (-fno-stack-protector)"
afl-gcc -O1 -g -fno-stack-protector -o vuln_plain vuln.c

echo "[+] built $WORK/vuln_asan and $WORK/vuln_plain"
