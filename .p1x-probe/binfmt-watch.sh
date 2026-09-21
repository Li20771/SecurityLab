#!/bin/bash
# N-01 watch: sample Ubuntu-side binfmt_misc state every 2s while Kali is started.
# READ-ONLY: only reads /proc. Writes report to file so it survives session loss.
L=/proc/sys/fs/binfmt_misc
OUT=/mnt/d/SecurityLab/.p1x-probe/binfmt-watch-OUTPUT.txt
{
  echo "start $(date '+%T.%N')"
  for i in $(seq 1 45); do
    n=$(ls $L 2>/dev/null | wc -l)
    w=$([ -f $L/WSLInterop ] && echo P || echo A)
    q=$(ls $L 2>/dev/null | grep -c qemu)
    m=$(stat -c %y $L 2>/dev/null)
    pg=$(grep binfmt /proc/self/mountinfo 2>/dev/null | awk '{print $1, $3, $5, $7}')
    echo "$(date '+%T.%N') count=$n WSLInterop=$w qemu=$q mtime=\"$m\" mount(id,dev,path,peer)=\"$pg\""
    sleep 2
  done
  echo "end $(date '+%T.%N')"
  echo "WATCH_END"
} > "$OUT" 2>&1
cat "$OUT"
