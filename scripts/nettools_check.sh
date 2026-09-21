#!/usr/bin/env bash
# nettools_check.sh — 网络安全工具最小功能核验（loopback，不触网）
# 验证 nmap 的 TCP connect 扫描、ncat / socat 的端到端收发；iptables/nft 只读探测。
set -u
PASS=0; FAIL=0
ok()   { echo "[PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

P1=34311; P2=34322; P3=34333
cleanup() {
    [ -n "${LP1:-}" ] && kill "$LP1" 2>/dev/null
    [ -n "${LP2:-}" ] && kill "$LP2" 2>/dev/null
    [ -n "${LP3:-}" ] && kill "$LP3" 2>/dev/null
}
trap cleanup EXIT

echo "=== ncat loopback echo ==="
if command -v ncat >/dev/null 2>&1; then
    ncat -l 127.0.0.1 "$P1" >/tmp/nt_ncat.out 2>&1 & LP1=$!
    sleep 1.0
    printf 'NCAT_ONEWAY_OK' | ncat -w 3 127.0.0.1 "$P1" 2>/dev/null
    sleep 0.8
    if grep -q 'NCAT_ONEWAY_OK' /tmp/nt_ncat.out; then
        ok "ncat client->listener end-to-end delivery"
    else
        bad "ncat loopback delivery failed (got '$(cat /tmp/nt_ncat.out 2>/dev/null)')"
    fi
else
    bad "ncat missing"
fi

echo "=== socat loopback relay ==="
if command -v socat >/dev/null 2>&1; then
    socat TCP-LISTEN:"$P2",bind=127.0.0.1,reuseaddr - >/tmp/nt_socat.out 2>&1 & LP2=$!
    sleep 0.8
    printf 'SOCAT_RELAY_OK' | socat - TCP:127.0.0.1:"$P2" 2>/dev/null
    sleep 0.5
    if grep -q 'SOCAT_RELAY_OK' /tmp/nt_socat.out; then ok "socat relay loopback"; else bad "socat relay failed"; fi
else
    bad "socat missing"
fi

echo "=== nmap TCP connect scan against local listener ==="
if command -v nmap >/dev/null 2>&1; then
    ncat -l 127.0.0.1 "$P3" -c 'cat' >/dev/null 2>&1 & LP3=$!
    sleep 0.8
    OUT=$(nmap -sT -p "$P3" 127.0.0.1 2>/dev/null)
    if printf '%s' "$OUT" | grep -Eq "^${P3}/tcp[[:space:]]+open"; then
        ok "nmap -sT reports listener port $P3 open"
    else
        bad "nmap did not see port $P3 open"
    fi
else
    bad "nmap missing"
fi

echo "=== capture / packet tools presence ==="
for t in tcpdump tshark nc; do
    command -v "$t" >/dev/null 2>&1 && ok "$t present" || bad "$t missing"
done

echo "=== firewall read-only probe (WSL 通常无独立规则集) ==="
if command -v nft >/dev/null 2>&1; then
    if nft list ruleset >/dev/null 2>&1; then ok "nft list ruleset works (ruleset present/empty)"; else
        echo "[WARN] nft present but 'list ruleset' unavailable without privileges (expected on WSL)"; fi
fi
if command -v iptables >/dev/null 2>&1; then
    if iptables -S >/dev/null 2>&1; then ok "iptables -S works"; else
        echo "[WARN] iptables present but listing needs root (expected on WSL)"; fi
fi

echo
echo "NETTOOLS: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && echo "NETTOOLS_CHECK=PASS" || echo "NETTOOLS_CHECK=FAIL"
exit "$FAIL"