#!/bin/bash
# Diagnose why systemd-run self-heal cannot re-add WSLInterop while
# an interactive root shell can.
B=/proc/sys/fs/binfmt_misc
OUT=/mnt/d/SecurityLab/.p1x-probe/svc-vs-shell-OUTPUT.txt
{
  echo "=== delete WSLInterop ==="
  echo -1 > $B/WSLInterop && echo "deleted"
  ls $B | grep -c WSLInterop

  echo "=== write from a systemd service, capturing stderr ==="
  cat > /tmp/n01-svcwrite.sh <<'EOF'
#!/bin/bash
echo "uid=$(id -u) euid=$(id -e)"
echo "capabilities: $(grep CapEff /proc/self/status)"
echo "no_new_privs: $(grep NoNewPrivs /proc/self/status)"
echo ':WSLInterop:M::MZ::/init:P' > /proc/sys/fs/binfmt_misc/register
echo "write_rc=$?"
ls /proc/sys/fs/binfmt_misc | grep -c WSLInterop
EOF
  chmod +x /tmp/n01-svcwrite.sh
  systemd-run --unit=n01-svcwrite --quiet --wait /tmp/n01-svcwrite.sh 2>&1
  echo "systemd-run rc=$?"
  journalctl -u n01-svcwrite --no-pager 2>&1 | tail -12
  rm -f /tmp/n01-svcwrite.sh

  echo "=== state after service write ==="
  ls $B | grep -c WSLInterop
  echo "PROBE_DONE"
} > "$OUT" 2>&1
cat "$OUT"
