#!/bin/bash
# binfmt-selfheal.sh — N-01 mitigation (v2.3, 2026-09-21)
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
# v2.3 fail-closed hardening (2026-09-21 engineering close):
#   additive-only means additive-ONLY: when binfmt-register-additive.py is
#   missing or fails, this script logs a clear error and reports failure.
#   It NEVER falls back to /usr/lib/systemd/systemd-binfmt, because that
#   fallback flushes the shared registry and re-triggers the cross-distro
#   flush oscillation this design exists to prevent.
#
# This script is idempotent and additive-only (never unregisters anything):
#   * WSLInterop absence is a heal trigger ON ITS OWN;
#   * default mode ("full", used by the .path unit): six quick attempts win
#     the boot-time flush race, then a 40 s slow window covers the other
#     distro's shutdown flush;
#   * --quick mode (used by the .timer safety net): one single check, for
#     cases where the .path watch silently stops firing across a remount;
#   * flock: overlapping triggers never run concurrently.
#
# Test seams (defaults unchanged; overridden only by
# configs/binfmt/tests/selfheal_failclosed_test.sh):
#   BINFMT_REGISTRY  binfmt_misc mount point      (default /proc/sys/fs/binfmt_misc)
#   BINFMT_LOCK      flock file                   (default /run/binfmt-selfheal.lock)
#   BINFMT_HELPER    additive helper path         (default /usr/local/sbin/binfmt-register-additive.py)
#   BINFMT_D_DIRS    binfmt.d conf directories    (default /etc/binfmt.d /run/binfmt.d /usr/lib/binfmt.d)
set -u

B=${BINFMT_REGISTRY:-/proc/sys/fs/binfmt_misc}
TAG=binfmt-selfheal
LOCK=${BINFMT_LOCK:-/run/binfmt-selfheal.lock}
HELPER=${BINFMT_HELPER:-/usr/local/sbin/binfmt-register-additive.py}
D_DIRS=${BINFMT_D_DIRS:-/etc/binfmt.d /run/binfmt.d /usr/lib/binfmt.d}
MODE=${1:-full}

# Helper missing is a permanent condition: log the error once, then keep
# the heal loop alive (WSLInterop may still need healing) without spam.
HELPER_MISSING=0
# Helper execution failure: log once per run (boot-race retries may
# legitimately fail transiently); the final verdict decides the exit code.
HELPER_ERROR=0

exec 9>"$LOCK"
flock -n 9 || exit 0

# Registry not mounted (early boot / teardown) — nothing to do.
[ -e "$B/status" ] || exit 0

need_distro_entries() {
    local dir conf name
    for dir in $D_DIRS; do
        for conf in "$dir"/*.conf; do
            [ -e "$conf" ] || continue
            # entry name = first ':'-separated field of the registration line
            name=$(sed -n 's/^[[:space:]]*:\([^:]*\):.*/\1/p' "$conf" | head -n1)
            if [ -n "$name" ] && [ ! -e "$B/$name" ]; then
                return 0
            fi
        done
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
    # restores these if another distro blanks them. Add ONLY the missing
    # rules via the additive helper. v2.3: FAIL-CLOSED — if the helper is
    # missing or fails, report the error and NEVER fall back to
    # systemd-binfmt (its flush starts the cross-distro oscillation).
    if need_distro_entries; then
        if [ "$HELPER_MISSING" = "1" ]; then
            return 1
        fi
        if [ ! -x "$HELPER" ]; then
            HELPER_MISSING=1
            logger -t "$TAG" -p user.err \
                "binfmt-register-additive.py missing at $HELPER — fail-closed, NOT falling back to systemd-binfmt ($MODE)"
            return 1
        fi
        # 先保存 helper 的真实退出码，再进分支写日志——日志语句之前
        # 还有 `[ ... ]` 判定，直接写 $? 会拿到测试的退出码而不是 helper 的。
        helper_rc=0
        if "$HELPER" >/dev/null 2>&1; then
            :
        else
            helper_rc=$?
            # Boot-race retries make transient failures possible; log the
            # error once per run, the final verdict below is authoritative.
            if [ "$HELPER_ERROR" = "0" ]; then
                HELPER_ERROR=1
                logger -t "$TAG" -p user.err \
                    "binfmt-register-additive.py failed (rc=$helper_rc) — fail-closed, NOT falling back to systemd-binfmt ($MODE)"
            fi
            return 1
        fi
    fi
    return 0
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
[ "$HELPER_MISSING" = "1" ] && { logger -t "$TAG" -p user.err "additive helper missing — registry NOT restored (fail-closed)"; rc=1; }
[ "$rc" -eq 0 ] && logger -t "$TAG" "binfmt_misc registry verified/restored"
exit "$rc"
