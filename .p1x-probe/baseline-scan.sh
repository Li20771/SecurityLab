#!/bin/bash
# Phase 0 baseline scan (read-only). Ubuntu side.
echo "=== OS ==="; lsb_release -ds; uname -r
echo "=== systemd ==="; systemctl is-system-running 2>&1
systemctl --failed --no-pager 2>&1 | head -8
echo "=== vcan ==="; ip -brief link show vcan0 2>&1
echo "=== python venv ==="; ~/.venvs/network/bin/python --version
~/.venvs/network/bin/pip list 2>/dev/null | grep -Ei '^(can-isotp|cantools|pwntools|python-can|scapy|udsoncan|pytest|coverage|ruff|mypy)' || true
echo "=== binfmt ==="; ls /proc/sys/fs/binfmt_misc | wc -l
cat /proc/sys/fs/binfmt_misc/status 2>&1
echo "=== tools ==="
for t in gcc aarch64-linux-gnu-gcc arm-linux-gnueabihf-gcc gdb gdb-multiarch qemu-aarch64 qemu-arm binwalk r2 ghidra strace ltrace yara nmap tcpdump tshark socat afl-fuzz git docker docker-compose; do
  p=$(command -v "$t" 2>/dev/null)
  if [ -n "$p" ]; then echo "OK   $t -> $p"; else echo "MISS $t"; fi
done
echo "=== cross toolchain versions ==="
aarch64-linux-gnu-gcc --version 2>/dev/null | head -1 || echo "aarch64-gcc MISSING"
arm-linux-gnueabihf-gcc --version 2>/dev/null | head -1 || echo "armhf-gcc MISSING"
gdb-multiarch --version 2>/dev/null | head -1 || echo "gdb-multiarch MISSING"
echo "=== network ==="
ip route show default 2>&1
curl -sI --max-time 5 https://github.com 2>&1 | head -1 || echo "github direct: FAIL"
echo "=== git ssh ==="
GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new" git ls-remote git@github.com:Li20771/SecurityLab.git HEAD 2>&1 | head -3
echo "=== DSH web ==="
ss -tlnp 2>/dev/null | grep 3080 || echo "no 3080 listener"
echo "=== docker cli in Ubuntu ==="
ls -la /usr/bin/docker /usr/bin/docker-compose 2>&1
docker version 2>&1 | head -6
echo "=== disk ==="
df -h / /mnt/d 2>&1
echo "=== mem ==="
free -h | head -2
echo "SCAN_END"
