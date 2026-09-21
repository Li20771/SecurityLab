#!/bin/bash
# End-to-end self-heal test: delete two entries (WSLInterop + qemu-arm),
# then start the service and verify both are restored.
B=/proc/sys/fs/binfmt_misc
echo "before: WSLInterop=$(ls $B | grep -c '^WSLInterop$') qemu-arm=$(ls $B | grep -c '^qemu-arm$')"
echo -1 > $B/WSLInterop && echo "deleted WSLInterop"
echo -1 > $B/qemu-arm && echo "deleted qemu-arm"
echo "mid:    WSLInterop=$(ls $B | grep -c '^WSLInterop$') qemu-arm=$(ls $B | grep -c '^qemu-arm$')"
systemctl start binfmt-selfheal.service
echo "service_rc=$?"
echo "after:  WSLInterop=$(ls $B | grep -c '^WSLInterop$') qemu-arm=$(ls $B | grep -c '^qemu-arm$')"
echo "--- journal ---"
journalctl -t binfmt-selfheal --since '-1min' --no-pager
echo "TEST_END"
