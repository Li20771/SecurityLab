#!/bin/bash
# binfmt-selfheal.sh — N-01 mitigation (v2.2, 2026-09-21)
#
# Background (see docs/phases/phase-01-wsl.md and
# docs/FINAL_ENVIRONMENT_AUDIT_2026-09-20.md):
# In WSL2 every distro mounts the kernel-global binfmt_misc registry (same
# dev/inode, different mount namespaces/peer groups). Experiments on
# 2026-09-20 proved:
#   * Kali cold boot with DEFAULT protectBinfmt=true deletes only WSLInterop
#     (its WSL-generated drop-in runs `echo -1` then fails to re-create it);
#     qemu/python entries survive. v1 selfheal missed this because it only
#     healed when distro binfmt.d entries were gone.
#   * protectBinfmt=false is WORSE: Kali's fresh binfmt_misc mount blanks the
#     whole registry VM-wide (all qemu handlers too), and systemd .path
#     monitoring does NOT fire across that remount. (Setting reverted.)
#   * A second delete happens when the other distro's last session ends.
#
# v2.2 hot-share finding (2026-09-21, after Kali gained qemu-user-binfmt):
#   When two distros run AT ONCE they share one kernel-global registry. Each
#   distro's binfmt.d set differs slightly (Kali ships qemu-or1k, Ubuntu does
#   not; Ubuntu registers /usr/libexec/qemu-binfmt/*-binfmt-P, Kali /usr/bin/*).
#   v2.1 healed missing distro entries by exec'ing systemd-binfmt, which FLUSHES
#   the whole registry first - deleting WSLInterop. The other distro's .path then
#   fired, saw missing entries, flushed again ... a permanent ~10 s flush
#   oscillation (proven: it stops the instant Kali is terminated). v2.2 therefore
#   NEVER flushes: a Python helper (binfmt-register-additive.py) adds only the
#   single missing rule via raw bytes (qemu masks contain NUL, impossible from
#   shell). Cross-distro interpreter paths are reconciled with compat symlinks
#   (Kali: /usr/libexec/qemu-binfmt/*-binfmt-P -> /usr/bin/qemu-*).
#
# This script is idempotent and additive-only (never unregisters anything):
#   * WSLInterop absence is a heal trigger ON ITS OWN;
#   * default mode ("full", used by the .path unit): six quick attempts win
#     the boot-time flush race, then a 40 s slow window covers the other
#     distro's shutdown flush;
#   * --quick mode (used by the .timer safety net): one single check, for
#     cases where the .path watch silently stops firing across a remount;
#   * flock: overlapping triggers never run concurrently.
set -u

B=/proc/sys/fs/binfmt_misc
TAG=binfmt-selfheal
LOCK=/run/binfmt-selfheal.lock
MODE=${1:-full}

exec 9>"$LOCK"
flock -n 9 || exit 0

# Registry not mounted (early boot / teardown) — nothing to do.
[ -e "$B/status" ] || exit 0

need_distro_entries() {
    local conf name
    for conf in /etc/binfmt.d/*.conf /run/binfmt.d/*.conf /usr/lib/binfmt.d/*.conf; do
        [ -e "$conf" ] || continue
        # entry name = first ':'-separated field of the registration line
        name=$(sed -n 's/^[[:space:]]*:\([^:]*\):.*/\1/p' "$conf" | head -n1)
        if [ -n "$name" ] && [ ! -e "$B/$name" ]; then
            return 0
        fi
    done
    return 1
}

register_interop() {
    echo ':WSLInterop:M::MZ::/init:P' > "$B/register" 2>/dev/null
}

heal_once() {
    # WSLInterop is WSL-owned and WSL init re-adds it on every new session,
    # but already-open sessions stay broken until that happens, so heal it
    # explicitly here.
    if [ ! -e "$B/WSLInterop" ]; then
        if register_interop; then
            logger -t "$TAG" "re-registered WSLInterop ($MODE)"
        else
            logger -t "$TAG" -p user.notice "WSLInterop re-register failed ($MODE)"
        fi
    fi
    # Distro-local binfmt.d entries (qemu-*, python3.x, ...) — nothing else
    # restores these if another distro blanks them. v2.2: add ONLY the missing
    # rules, never run systemd-binfmt (it flushes the whole shared registry and
    # starts the cross-distro flush oscillation).
    if need_distro_entries; then
        if [ -x /usr/local/sbin/binfmt-register-additive.py ]; then
            /usr/local/sbin/binfmt-register-additive.py >/dev/null 2>&1
        else
            # Fallback (older deploy): direct systemd-binfmt flushes, acceptable
            # at boot but not during steady-state hot-share.
            /usr/lib/systemd/systemd-binfmt 2>/dev/null
        fi
    fi
}

if [ "$MODE" = "--quick" ] || [ "$MODE" = "quick" ]; then
    heal_once
    if [ ! -e "$B/WSLInterop" ] || need_distro_entries; then
        exit 1
    fi
    exit 0
fi

# Full mode (path-triggered).
# Phase 1: immediate retries for the boot-time flush storm (~6 s).
for _ in 1 2 3 4 5 6; do
    heal_once
    sleep 1
done

# Phase 2: slow window covering the other distro's SHUTDOWN flush (~40 s).
# A path event that arrives while this service is active does not start a new
# instance (and flock would reject it), so keep checking inside this run.
for _ in 1 2 3 4 5 6 7 8; do
    sleep 5
    if [ ! -e "$B/WSLInterop" ] || need_distro_entries; then
        heal_once
    fi
done

rc=0
[ ! -e "$B/WSLInterop" ] && { logger -t "$TAG" -p user.warning "WSLInterop still missing after heal window"; rc=1; }
need_distro_entries && { logger -t "$TAG" -p user.warning "binfmt.d entries still missing after heal window"; rc=1; }
[ "$rc" -eq 0 ] && logger -t "$TAG" "binfmt_misc registry verified/restored"
exit "$rc"
