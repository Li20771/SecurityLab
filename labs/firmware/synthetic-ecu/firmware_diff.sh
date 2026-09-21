#!/usr/bin/env bash
#
# Firmware-diff exercise: extract the two SYNTHETIC images and compare them.
# Demonstrates the workflow used when tracking config/credential/program changes
# between firmware revisions.
#
# Usage: bash firmware_diff.sh
set +e
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${WORK:-/tmp}/syn-ecu-diff"
rm -rf "$BASE"; mkdir -p "$BASE"

for V in v1 v2; do
    IMG="$LAB_DIR/build/syn-ecu-$V.squashfs"
    [ -f "$IMG" ] || { echo "$IMG missing - run build_fw.sh all" >&2; exit 2; }
    unsquashfs -d "$BASE/$V" "$IMG" >/dev/null 2>&1
done

echo "############ file-level diff (diff -rq) ############"
diff -rq "$BASE/v1" "$BASE/v2" | sed "s#$BASE/##"

echo
echo "############ config.ini diff ############"
diff -u "$BASE/v1/etc/syn_ecu/config.ini" "$BASE/v2/etc/syn_ecu/config.ini" || true

echo
echo "############ network.conf diff ############"
diff -u "$BASE/v1/etc/syn_ecu/network.conf" "$BASE/v2/etc/syn_ecu/network.conf" || true

echo
echo "############ embedded program version-string diff ############"
strings "$BASE/v1/usr/bin/ecu-demo" | grep 'SYN-ECU v' | sed 's/^/v1: /'
strings "$BASE/v2/usr/bin/ecu-demo" | grep 'SYN-ECU v' | sed 's/^/v2: /'

echo
echo "############ credential change summary ############"
for V in v1 v2; do
    echo "[$V]"
    grep -aE 'admin_password|api_token' "$BASE/$V/etc/syn_ecu/config.ini" | sed 's/^/  /'
done
echo FIRMWARE_DIFF_DONE
