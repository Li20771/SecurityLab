#!/bin/bash
# Final N-01 acceptance v2 — checks run AFTER Kali boot churn settles,
# from whatever session runs this (WSL re-registers WSLInterop per session).
set -u
B=/proc/sys/fs/binfmt_misc
OUT=/mnt/d/SecurityLab/.p1x-probe/n01-acceptance2-OUTPUT.txt
{
  echo "=== registry ==="
  echo "count=$(ls $B | wc -l)"
  echo "WSLInterop=$(ls $B | grep -c '^WSLInterop$')  qemu=$(ls $B | grep -c 'qemu-')  python3.12=$(ls $B | grep -c '^python3.12$')"
  echo "=== functional: WSL interop ==="
  if /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "Write-Output INTEROP_OK" 2>/dev/null; then
    echo "interop: OK"
  else
    echo "interop: FAIL"
  fi
  echo "=== functional: ARM64 via qemu binfmt ==="
  cd /mnt/d/SecurityLab/labs/reverse/arm64 2>/dev/null || cd /tmp
  if [ -f hello_arm64 ]; then
    ./hello_arm64 2>&1 | head -3
  else
    echo "(hello_arm64 binary not found — compile check only)"
  fi
  echo "ACCEPTANCE2_DONE"
} > "$OUT" 2>&1
cat "$OUT"
