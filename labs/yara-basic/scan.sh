#!/usr/bin/env bash
#
# Minimal YARA exercise: compile the teaching rules, scan a known-positive and
# a known-clean sample, and assert true-positive / true-negative behaviour.
# Also scans the synthetic ECU firmware ELF if it has been built/extracted.
#
# Usage: bash scan.sh
set +e
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE="$LAB_DIR/rules/demo.yar"
SAMPLES="$LAB_DIR/samples"
FAIL=0

command -v yara >/dev/null || { echo "yara not installed (apt: yara)" >&2; exit 2; }

echo "############ rule compile + directory scan ############"
yara "$RULE" "$SAMPLES"
echo
echo "############ matched strings (positive sample) ############"
yara -s "$RULE" "$SAMPLES/sample_hit.txt"

hit()  { yara "$RULE" "$1" | grep -q "$2"; }   # rule $2 fires on file $1

echo
echo "############ assertions ############"
if hit "$SAMPLES/sample_hit.txt" SecurityLab_Synthetic_Beacon; then
  echo "TRUE_POSITIVE(beacon) = PASS"; else echo "TRUE_POSITIVE(beacon) = FAIL"; FAIL=1; fi
if hit "$SAMPLES/sample_ecu.txt" SecurityLab_Synthetic_EcuFirmware; then
  echo "TRUE_POSITIVE(ecu)    = PASS"; else echo "TRUE_POSITIVE(ecu)    = FAIL"; FAIL=1; fi
if [ -z "$(yara "$RULE" "$SAMPLES/sample_clean.txt")" ]; then
  echo "TRUE_NEGATIVE(clean)  = PASS"; else echo "TRUE_NEGATIVE(clean)  = FAIL"; FAIL=1; fi

echo
echo "############ optional: scan synthetic ECU firmware ELF ############"
ELF=""
for c in /tmp/syn-ecu-analyze-v1/root/usr/bin/ecu-demo \
         /tmp/syn-ecu-build/rootfs-v1/usr/bin/ecu-demo \
         "$LAB_DIR/../firmware/synthetic-ecu/build/ecu-demo"; do
    [ -x "$c" ] && ELF="$c" && break
done
if [ -n "$ELF" ]; then
  echo "scanning $ELF"
  yara "$RULE" "$ELF"
else
  echo "(no extracted synthetic ELF found; run firmware/synthetic-ecu first)"
fi

echo
[ "$FAIL" = 0 ] && echo "YARA_RESULT=PASS" || echo "YARA_RESULT=FAIL"
exit $FAIL
