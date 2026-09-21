#!/bin/bash
# Final N-01 acceptance: cold-boot Kali while watching Ubuntu's registry.
# PASS criteria: after Kali boot settles, Ubuntu registry has all 33 entries
# (WSLInterop + python3.12 + 29 qemu) without any manual repair.
B=/proc/sys/fs/binfmt_misc
OUT=/mnt/d/SecurityLab/.p1x-probe/n01-acceptance-OUTPUT.txt
{
  echo "=== pre: Ubuntu registry ==="
  ls $B | wc -l
  echo "=== terminate + cold boot Kali ==="
  # (terminated from Windows side by caller)
  echo "waiting 40s for Kali boot + self-heal to settle..."
  sleep 40
  echo "=== post: Ubuntu registry ==="
  ls $B | wc -l
  ls $B | tr '\n' ' '; echo
  echo "WSLInterop present: $(ls $B | grep -c '^WSLInterop$')"
  echo "qemu count: $(ls $B | grep -c qemu)"
  echo "python3.12 present: $(ls $B | grep -c '^python3.12$')"
  echo "=== self-heal journal ==="
  journalctl -t binfmt-selfheal --since '-3min' --no-pager
  echo "=== functional: interop ==="
  /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "Write-Output INTEROP_WORKS" 2>&1 | tail -2
  echo "ACCEPTANCE_DONE"
} > "$OUT" 2>&1
cat "$OUT"
