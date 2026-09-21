#!/bin/bash
# Phase 0 baseline scan (read-only). Kali side.
echo "=== OS ==="; ( . /etc/os-release && echo "$PRETTY_NAME" ); uname -r
echo "=== systemd ==="; systemctl is-system-running 2>&1
systemctl --failed --no-pager 2>&1 | head -10
echo "=== user@1000 ==="; systemctl status user@1000.service --no-pager 2>&1 | head -12
echo "=== binfmt ==="; ls /proc/sys/fs/binfmt_misc | tr '\n' ' '; echo
echo "=== binfmt.d ==="; ls /usr/lib/binfmt.d/ /etc/binfmt.d/ 2>&1
echo "=== tools ==="
for t in gcc gdb python3 git nmap masscan hydra metasploit-framework binwalk; do
  p=$(command -v "$t" 2>/dev/null)
  if [ -n "$p" ]; then echo "OK   $t -> $p"; else echo "MISS $t"; fi
done
echo "=== user lingering ==="; loginctl show-user lilike 2>&1 | grep -Ei 'linger|state' || true
echo "=== journal: user@1000 last ==="
journalctl -b -u user@1000.service --no-pager 2>&1 | tail -8
echo "SCAN_END"
