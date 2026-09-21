#!/usr/bin/env bash
#
# Build SYNTHETIC ECU firmware images (v1 / v2) for the firmware-analysis lab.
#
# IMPORTANT: Everything produced here is hand-built teaching material.
# It is NOT real vehicle/ECU firmware and contains no real credentials.
#
# v1 and v2 differ on purpose (program version, config, credentials, network
# settings, an added feature file) so they can drive a firmware-diff exercise.
#
# The rootfs is staged in the WSL native filesystem ($STAGE, default /tmp) so
# that mksquashfs records correct Linux ownership/permissions; only the final
# squashfs image is written back to the lab build/ dir on /mnt/d.
#
# Usage: bash build_fw.sh [v1|v2|all]
set -eu

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$LAB_DIR/src/ecu_demo.c"
OUT="$LAB_DIR/build"
STAGE="${STAGE:-/tmp}/syn-ecu-build"
CC=aarch64-linux-gnu-gcc
MKSQ=mksquashfs

mkdir -p "$OUT"

build_one() {
    local VER="$1"          # v1 | v2
    local ROOT="$STAGE/rootfs-$VER"
    local IMG="$OUT/syn-ecu-$VER.squashfs"

    case "$VER" in
        v1) local FWVER="SYN-ECU v1.0"
            local PASS="DemoPass!2024"
            local TOKEN="syn_token_aaa_1111"
            local IP="192.168.10.10"
            local BANNER="SYNTHETIC ECU boot banner - revision 1" ;;
        v2) local FWVER="SYN-ECU v2.0"
            local PASS="DemoPass!2025"
            local TOKEN="syn_token_bbb_2222"
            local IP="192.168.10.11"
            local BANNER="SYNTHETIC ECU boot banner - revision 2" ;;
        *) echo "unknown version $VER" >&2; exit 2 ;;
    esac

    echo "=== building $VER ($FWVER) ==="
    rm -rf "$ROOT"
    mkdir -p "$ROOT"/{bin,sbin,usr/bin,etc/init.d,etc/syn_ecu,proc,sys,dev,tmp,var/log}

    # static AArch64 diagnostic program
    "$CC" -static -O0 -g \
        -DFW_VERSION_STR="\"$FWVER\"" \
        -o "$ROOT/usr/bin/ecu-demo" "$SRC"

    # /etc config (SYNTHETIC, intentionally fake credentials)
    cat > "$ROOT/etc/syn_ecu/config.ini" <<EOF
# SYNTHETIC demo config - not real credentials
device=syn-ecu-demo
model=demo-gateway
diag_can=vcan0
admin_user=admin
admin_password=$PASS
api_token=$TOKEN
EOF

    cat > "$ROOT/etc/syn_ecu/network.conf" <<EOF
# SYNTHETIC network configuration
iface=eth0
ipaddr=$IP
netmask=255.255.255.0
gateway=192.168.10.1
diag_udp=13400
EOF

    [ "$VER" = "v2" ] && cat > "$ROOT/etc/syn_ecu/feature.conf" <<EOF
# added in v2
secure_boot=on
uds_0x27_seedkey=hardened
EOF

    # startup scripts
    cat > "$ROOT/etc/init.d/rcS" <<'EOF'
#!/bin/sh
# SYNTHETIC init script
mount -t proc proc /proc 2>/dev/null
mount -t sysfs sysfs /sys 2>/dev/null
echo "starting synthetic ecu-demo"
/usr/bin/ecu-demo
EOF
    chmod +x "$ROOT/etc/init.d/rcS"

    cat > "$ROOT/etc/inittab" <<'EOF'
# SYNTHETIC inittab
::sysinit:/etc/init.d/rcS
::respawn:/bin/sh
EOF

    # passwd / shadow placeholders (locked / teaching only)
    cat > "$ROOT/etc/passwd" <<'EOF'
root:x:0:0:root:/root:/bin/sh
admin:x:1000:1000:demo admin:/home/admin:/bin/sh
EOF
    cat > "$ROOT/etc/shadow" <<'EOF'
root:!:19000:0:99999:7:::
admin:$1$synsalt$0123456789abcdefABCDEF01234567.:19000:0:99999:7:::
EOF
    chmod 0600 "$ROOT/etc/shadow"

    echo "$BANNER" > "$ROOT/etc/banner"

    # a SUID-style helper is intentionally NOT created; the analyzer should
    # report zero SUID files on this clean synthetic image.

    rm -f "$IMG"
    "$MKSQ" "$ROOT" "$IMG" -noappend -no-progress -comp gzip >/dev/null
    echo "wrote $IMG ($(stat -c%s "$IMG") bytes)"
}

case "${1:-all}" in
    v1) build_one v1 ;;
    v2) build_one v2 ;;
    all) build_one v1; build_one v2 ;;
    *) echo "usage: bash build_fw.sh [v1|v2|all]" >&2; exit 2 ;;
esac

echo "BUILD_DONE"
