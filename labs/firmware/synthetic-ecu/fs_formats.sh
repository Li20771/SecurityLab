#!/usr/bin/env bash
#
# Verify JFFS2 / UBIFS / UBI image creation and binwalk identification using
# the SYNTHETIC rootfs (build_fw.sh stages it under /tmp/syn-ecu-build).
#
# Creation toolchain: mtd-utils (mkfs.jffs2, mkfs.ubifs, ubinize, jffs2dump).
# NOTE: extracting JFFS2/UBIFS on WSL2 without real MTD devices needs external
# userspace unpackers (jefferson for JFFS2, ubireader for UBI/UBIFS); those are
# recorded as optional. SquashFS extraction is fully covered by unsquashfs.
#
# Usage: bash fs_formats.sh
set +e
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${STAGE_ROOT:-/tmp/syn-ecu-build/rootfs-v1}"
OUT="$LAB_DIR/build"
mkdir -p "$OUT"
[ -d "$ROOT" ] || { echo "stage rootfs missing: $ROOT (run build_fw.sh first)" >&2; exit 2; }

echo "############ JFFS2 ############"
mkfs.jffs2 --little-endian --squash -r "$ROOT" -o "$OUT/syn-ecu-v1.jffs2" && echo "jffs2 built"
ls -l "$OUT/syn-ecu-v1.jffs2"
jffs2dump "$OUT/syn-ecu-v1.jffs2" 2>&1 | head -3
echo "--- binwalk JFFS2 ---"; binwalk "$OUT/syn-ecu-v1.jffs2" 2>/dev/null | grep -iE 'JFFS2|DESCRIPTION|DECIMAL' | head

echo
echo "############ UBIFS ############"
mkfs.ubifs -m 2048 -e 126976 -c 128 -r "$ROOT" -o "$OUT/syn-ecu-v1.ubifs" && echo "ubifs built"
ls -l "$OUT/syn-ecu-v1.ubifs"
echo "--- binwalk UBIFS ---"; binwalk "$OUT/syn-ecu-v1.ubifs" 2>/dev/null | grep -iE 'UBIFS|UBI|DESCRIPTION|DECIMAL' | head

echo
echo "############ UBI (ubinize) ############"
cat > "$OUT/ubinize.ini" <<EOF
[ubifs]
mode=ubi
image=$OUT/syn-ecu-v1.ubifs
vol_id=0
vol_size=8MiB
vol_type=dynamic
vol_name=rootfs
EOF
ubinize -o "$OUT/syn-ecu-v1.ubi" -m 2048 -p 128KiB "$OUT/ubinize.ini" && echo "ubi built"
ls -l "$OUT/syn-ecu-v1.ubi"
echo "--- binwalk UBI ---"; binwalk "$OUT/syn-ecu-v1.ubi" 2>/dev/null | grep -iE 'UBI|DESCRIPTION|DECIMAL' | head

echo
echo "############ unpacker availability (optional on WSL) ############"
for t in jefferson ubireader_extract_files; do
    printf '%-28s %s\n' "$t" "$(command -v "$t" || echo 'not installed (optional)')"
done
echo FS_FORMATS_DONE
