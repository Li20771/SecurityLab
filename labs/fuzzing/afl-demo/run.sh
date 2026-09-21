#!/usr/bin/env bash
#
# End-to-end AFL++ exercise on the synthetic target:
#   build instrumented binary -> seed corpus -> time-boxed fuzz -> triage
#   -> deterministic crash reproduction.
#
# Everything runs in the WSL native filesystem ($WORK, default /tmp/afl-demo)
# because AFL++ shared-memory/locking and throughput are poor on /mnt/d.
#
# Usage: bash run.sh [fuzz-seconds]   (default 60)
set +e
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-/tmp}/afl-demo"
SECS="${1:-60}"
mkdir -p "$WORK"; cd "$WORK" || exit 3

# 1. build
bash "$LAB_DIR/build.sh" || { echo "build failed" >&2; exit 2; }
TARGET=vuln_asan
[ -x "$TARGET" ] || TARGET=vuln_plain

# 2. seed corpus (valid, non-crashing inputs)
rm -rf in out; mkdir -p in
printf 'AFL\x02hi' > in/seed1
printf 'AFL\x00'   > in/seed2

# 3. time-boxed fuzz
export AFL_NO_UI=1 AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_AFFINITY=1
echo "[*] fuzzing $TARGET for ${SECS}s ..."
afl-fuzz -i in -o out -V "$SECS" -- "./$TARGET" @@ >fuzz.log 2>&1
echo "[*] afl-fuzz exit=$?"
grep -E 'unique crashes|cycles done' fuzz.log | tail -4

# 4. triage
shopt -s nullglob
CRASHES=(out/default/crashes/id*)
NC=${#CRASHES[@]}
echo "[*] unique crash files: $NC"
[ "$NC" -eq 0 ] && { echo "RESULT=FAIL no crash found (raise time budget)" >&2; exit 1; }

# 5. deterministic reproduction (same crash file must crash twice, same signal)
C="${CRASHES[0]}"
echo "[*] reproducing: $(basename "$C") ($(stat -c%s "$C") bytes)"
xxd "$C" | head -2
RCS=()
for i in 1 2; do
    ASAN_OPTIONS=abort_on_error=1:symbolize=0 "./$TARGET" "$C" >rep$i.log 2>&1
    RCS+=($?)
    grep -E 'AddressSanitizer|stack-buffer-overflow|stack smashing' rep$i.log | head -1
done
echo "[*] replay exit codes: ${RCS[0]} ${RCS[1]}"
if [ "${RCS[0]}" -gt 128 ] && [ "${RCS[0]}" = "${RCS[1]}" ]; then
    echo "RESULT=PASS crash discovered and deterministically reproduced ($TARGET, sig $((RCS[0]-128)))"
    exit 0
fi
echo "RESULT=UNCERTAIN replay codes differ or not a signal crash"
exit 1
