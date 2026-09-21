#!/usr/bin/env bash
#
# Analyze a SYNTHETIC ECU firmware image end to end:
#   binwalk identification -> unsquashfs extraction -> filesystem inventory
#   -> ELF architecture id -> strings/credential hunt -> startup/network review
#   -> permission/SUID audit -> QEMU user-mode execution of the static ELF
#
# Extraction uses the WSL native fs (/tmp) so Linux permissions are accurate.
#
# Usage: bash analyze.sh [v1|v2]   (default v1)
set +e
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VER="${1:-v1}"
IMG="$LAB_DIR/build/syn-ecu-$VER.squashfs"
WORK="${WORK:-/tmp}/syn-ecu-analyze-$VER"

[ -f "$IMG" ] || { echo "image not found: $IMG (run build_fw.sh first)" >&2; exit 2; }
rm -rf "$WORK"; mkdir -p "$WORK"

echo "############ 1. file + binwalk identification ############"
file "$IMG"
binwalk "$IMG" 2>/dev/null | sed -n '1,6p'

echo
echo "############ 2. squashfs superblock ############"
unsquashfs -s "$IMG" 2>/dev/null | grep -E 'Compression|Block size|Filesystem size|inode'

echo
echo "############ 3. extraction (native fs, accurate perms) ############"
unsquashfs -d "$WORK/root" "$IMG" >/dev/null 2>&1 && echo "extracted -> $WORK/root"
find "$WORK/root" -type f | sed "s#$WORK/root/##" | sort

echo
echo "############ 4. ELF identification ############"
ELF="$WORK/root/usr/bin/ecu-demo"
file "$ELF"
readelf -h "$ELF" 2>/dev/null | grep -E 'Class|Machine|Type|Entry'

echo
echo "############ 5. strings / credential hunt (SYNTHETIC markers) ############"
echo "--- plaintext secrets in /etc ---"
grep -raniE 'password|token|secret|admin' "$WORK/root/etc" 2>/dev/null
echo "--- hash material in shadow ---"
grep -anE '^[^:]+:\$|^[^:]+:[^!:]' "$WORK/root/etc/shadow" 2>/dev/null
echo "--- interesting strings inside the ELF ---"
strings "$ELF" | grep -aiE 'SYN-ECU|UDS|vcan0|password|token' | head

echo
echo "############ 6. startup / network configuration ############"
echo "--- init.d/rcS ---"; sed 's/^/  /' "$WORK/root/etc/init.d/rcS"
echo "--- network.conf ---"; sed 's/^/  /' "$WORK/root/etc/syn_ecu/network.conf"

echo
echo "############ 7. permission / SUID audit ############"
SUID=$(find "$WORK/root" -xdev -type f -perm -4000 2>/dev/null)
if [ -z "$SUID" ]; then echo "SUID files: 0 (clean)"; else echo "$SUID"; fi
echo "shadow perms: $(stat -c '%a %U:%G' "$WORK/root/etc/shadow")"

echo
echo "############ 8. QEMU user-mode execution (static AArch64) ############"
OUT=$(qemu-aarch64 "$ELF" 2>&1)
RC=$?
echo "$OUT"
echo "$OUT" | grep -q "boot: SYN-ECU" && [ "$RC" = 0 ] \
    && echo "QEMU_EXEC=PASS" || echo "QEMU_EXEC=FAIL(rc=$RC)"

echo
echo "############ 9. metadata ############"
echo "image mtime: $(stat -c '%y' "$IMG")"
echo "file count : $(find "$WORK/root" -type f | wc -l)"
echo "ANALYZE_DONE($VER)"
