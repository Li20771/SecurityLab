#!/bin/bash
# N-01 forensics: does Kali's systemd-binfmt flush the binfmt_misc registry?
# READ-ONLY: only reads /proc, systemctl status, and the journal.
# Writes its report to a file so the result survives interop loss.
L=/proc/sys/fs/binfmt_misc
OUT=/mnt/d/SecurityLab/.p1x-probe/kali-probe-OUTPUT.txt
show() {
  echo "  handler_count = $(ls $L 2>/dev/null | wc -l)"
  echo "  list          = $(ls $L 2>/dev/null | tr '\n' ' ')"
  echo "  WSLInterop    = $( [ -f $L/WSLInterop ] && echo PRESENT || echo ABSENT )"
  echo "  qemu_handlers = $(ls $L 2>/dev/null | grep -c qemu)"
  echo "  dir_mtime     = $(stat -c %y $L 2>/dev/null)"
}
{
echo "########## ENTRY (Kali just started) ##########"
date '+%Y-%m-%d %H:%M:%S.%N'; show
echo
echo "########## Q1: does KALI have the WSL protectBinfmt drop-in? ##########"
echo "--- /run/systemd/generator/systemd-binfmt.service.d/ ---"
ls -la /run/systemd/generator/systemd-binfmt.service.d/ 2>&1
echo "--- override.conf content ---"
cat /run/systemd/generator/systemd-binfmt.service.d/override.conf 2>&1
echo
echo "########## Q2: effective ExecStart / ExecStop ##########"
systemctl show systemd-binfmt.service -p ExecStart -p ExecStop -p Result -p ExecMainStartTimestamp -p ExecMainExitTimestamp 2>&1
echo
echo "########## Q3: enabled / active ##########"
echo "is-enabled: $(systemctl is-enabled systemd-binfmt.service 2>&1)"
echo "is-active : $(systemctl is-active systemd-binfmt.service 2>&1)"
echo
echo "########## Q4: binfmt.d content ##########"
echo "--- /usr/lib/binfmt.d ---"; ls -1 /usr/lib/binfmt.d/ 2>&1 | head -40
echo "--- /etc/binfmt.d ---";   ls -1 /etc/binfmt.d/ 2>&1
echo "--- /run/binfmt.d ---";   ls -1 /run/binfmt.d/ 2>&1
echo
echo "########## Q5: journal: systemd-binfmt this boot ##########"
journalctl -b -u systemd-binfmt.service --no-pager 2>&1 | tail -30
echo "--- journal grep binfmt/register ---"
journalctl -b --no-pager 2>&1 | grep -Ei 'binfmt|Interpreter|register' | tail -30
echo
echo "########## Q6: FINAL measurement ##########"
date '+%Y-%m-%d %H:%M:%S.%N'; show
echo "PROBE_END"
} > "$OUT" 2>&1
cat "$OUT"
