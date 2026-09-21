#!/bin/bash
# Phase 2 diagnosis: Kali user@1000.service + failed units
echo "=== is-system-running ==="; systemctl is-system-running 2>&1
echo "=== failed ==="; systemctl --failed --no-pager 2>&1
echo "=== user@1000 status ==="; systemctl status user@1000.service --no-pager 2>&1 | head -20
echo "=== user@1000 journal (this boot) ==="; journalctl -b -u user@1000.service --no-pager 2>&1 | tail -25
echo "=== user-runtime-dir ==="; systemctl status user-runtime-dir@1000.service --no-pager 2>&1 | head -12
echo "=== /run/user/1000 ==="; ls -la /run/user/ 2>&1; ls -la /run/user/1000 2>&1 | head
echo "=== tpm-udev ==="; systemctl status tpm-udev.service --no-pager 2>&1 | head -12
echo "=== accounts-daemon ==="; systemctl status accounts-daemon.service --no-pager 2>&1 | head -12
echo "=== lingering? ==="; loginctl show-user lilike 2>&1 | grep -Ei 'linger|state|uid'
echo "=== /dev/kmsg / dmesg perms ==="; ls -la /dev/kmsg 2>&1
echo "DIAG_END"
