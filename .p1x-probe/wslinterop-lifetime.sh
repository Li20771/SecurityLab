#!/bin/bash
# N-01 decisive experiment: does a manually re-registered WSLInterop survive,
# when no self-heal service is involved? And do session vs systemd-service
# see the same binfmt_misc mount?
B=/proc/sys/fs/binfmt_misc
OUT=/mnt/d/SecurityLab/.p1x-probe/wslinterop-lifetime-OUTPUT.txt
{
  echo "=== phase 0: initial ==="
  date '+%T.%N'
  grep binfmt /proc/self/mountinfo
  ls $B | tr '\n' ' '; echo

  echo "=== phase 1: delete WSLInterop ==="
  echo -1 > $B/WSLInterop && echo "delete rc=0"
  ls $B | grep -c WSLInterop

  echo "=== phase 2: manual re-add ==="
  echo ':WSLInterop:M::MZ::/init:P' > $B/register && echo "readd rc=0"
  ls $B | grep -c WSLInterop
  cat $B/WSLInterop 2>&1

  echo "=== phase 3: watch 12s, no service ==="
  for i in $(seq 1 6); do
    echo "$(date '+%T.%N') present=$(ls $B | grep -c WSLInterop)"
    sleep 2
  done

  echo "=== phase 4: mountinfo from a systemd service ==="
  cat > /tmp/n01-mountinfo.sh <<'EOF'
#!/bin/bash
grep binfmt /proc/self/mountinfo
ls /proc/sys/fs/binfmt_misc | grep -c WSLInterop
EOF
  chmod +x /tmp/n01-mountinfo.sh
  systemd-run --unit=n01-mountinfo-probe --quiet --wait /tmp/n01-mountinfo.sh
  echo "--- service journal ---"
  journalctl -u n01-mountinfo-probe --no-pager 2>&1 | tail -6
  rm -f /tmp/n01-mountinfo.sh
  echo "PROBE_DONE"
} > "$OUT" 2>&1
cat "$OUT"
