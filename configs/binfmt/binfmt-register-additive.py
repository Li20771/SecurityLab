#!/usr/bin/env python3
# binfmt-register-additive.py - v2.2 companion (2026-09-21)
#
# Additive-only registrar for binfmt.d rules. Unlike /usr/lib/systemd/systemd-binfmt
# (which FLUSHES the whole kernel-global registry first, deleting WSLInterop and every
# other distro's handlers on shared WSL2 kernels), this tool ONLY adds rules that are
# currently missing. It never writes -1 to register/status, so it cannot start the
# cross-distro flush oscillation observed when Ubuntu and Kali both self-heal a shared
# binfmt_misc instance.
#
# CRITICAL: register lines are written VERBATIM as the escaped text found in binfmt.d
# (e.g. magic backslash-x-7f ELF ... backslash-x-00 ...). The kernel decodes the hex
# escapes itself. We must NOT decode escapes to raw bytes in userspace: qemu magics
# contain NUL (ELF EI_PAD at byte 7), and a register write is truncated at the first
# raw NUL, which drops the mask and makes the handler over-match every 64-bit
# little-endian ELF (observed incident on 2026-09-21, root-caused and fixed here).
#
# Usage: binfmt-register-additive.py [name ...]   (no args => consider all binfmt.d rules)
# Exit:  0 ok (nothing failed), 1 if any registration failed.
import sys, os, glob, errno

BASE = "/proc/sys/fs/binfmt_misc"
DIRS = ("/etc/binfmt.d", "/run/binfmt.d", "/usr/lib/binfmt.d")


def parse_rule(line):
    """Return (name, verbatim register bytes) or None.

    The line is passed through untouched (only surrounding whitespace stripped); the
    kernel owns escape decoding. We only need the name for the existence check.
    """
    line = line.strip()
    if not line.startswith(":"):
        return None
    parts = line.split(":")
    # ['', name, type, offset, magic, mask, interpreter, flags]
    if len(parts) < 7 or not parts[1] or not parts[6]:
        return None
    name = parts[1]
    return name, (line + "\n").encode("ascii", errors="strict")


def main():
    only = set(sys.argv[1:])
    confs = []
    for d in DIRS:
        confs += sorted(glob.glob(os.path.join(d, "*.conf")))

    if not os.path.exists(BASE + "/status"):
        print("binfmt_misc not mounted; nothing to do", file=sys.stderr)
        return 0

    added = skipped = failed = 0
    reg_fd = os.open(BASE + "/register", os.O_WRONLY)
    try:
        for cf in confs:
            try:
                with open(cf, "r", encoding="ascii", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            for raw in lines:
                if not raw.strip() or raw.lstrip()[:1] in ("#", ";"):
                    continue
                parsed = parse_rule(raw)
                if parsed is None:
                    continue
                name, data = parsed
                if only and name not in only:
                    continue
                # Defense in depth: a register payload must never contain a raw NUL;
                # binfmt.d encodes those as the four-character escape \x00.
                if b"\x00" in data:
                    failed += 1
                    print("REFUSE " + name + ": raw NUL in register payload",
                          file=sys.stderr)
                    continue
                if os.path.exists(os.path.join(BASE, name)):
                    skipped += 1
                    continue
                try:
                    os.write(reg_fd, data)
                    added += 1
                    print("ADD  " + name)
                except OSError as exc:
                    if exc.errno == errno.EEXIST:
                        skipped += 1
                    else:
                        failed += 1
                        print("FAIL " + name + ": " + str(exc), file=sys.stderr)
    finally:
        os.close(reg_fd)
    print("summary added=%d skipped=%d failed=%d" % (added, skipped, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
