# Synthetic ECU Firmware Analysis Lab

> **SYNTHETIC / TEACHING ONLY.** Everything in this lab is hand-built.
> It is **not** real vehicle or ECU firmware and contains **no real
> credentials**. It exists to exercise the full firmware-analysis pipeline on
> a target whose ground truth is known.

## Purpose

Build, analyze and diff minimal embedded-Linux style firmware images so the
following workflow is proven end to end before any real sample is introduced:

```
mksquashfs / mkfs.jffs2 / mkfs.ubifs / ubinize
        │  (build synthetic image)
        ▼
file + binwalk            filesystem identification / signature scan
        ▼
unsquashfs                filesystem extraction
        ▼
rootfs inventory          files, /etc, init scripts, network config
        ▼
ELF identification        file / readelf  (architecture, class, linkage)
        ▼
strings / credential hunt plaintext secrets, /etc/shadow, embedded markers
        ▼
permission / SUID audit   find -perm -4000, shadow perms, ownership
        ▼
QEMU user-mode execution  qemu-aarch64 ./usr/bin/ecu-demo  (static AArch64)
```

## Layout

```
synthetic-ecu/
├── README.md            this file
├── src/ecu_demo.c       synthetic diagnostic program (static AArch64)
├── build_fw.sh          build v1 / v2 squashfs images
├── analyze.sh           full analysis pipeline for one image
├── firmware_diff.sh     extract v1/v2 and diff them
├── fs_formats.sh        build + identify JFFS2 / UBIFS / UBI images
└── build/               generated images + staging (git-ignored)
```

The rootfs is staged under `/tmp/syn-ecu-build` (WSL native filesystem) so
mksquashfs records correct Linux ownership/permissions; only the finished
images are written to `build/` on `/mnt/d`.

## Requirements

- `aarch64-linux-gnu-gcc` (`gcc-aarch64-linux-gnu`)
- `qemu-aarch64` (`qemu-user` + `qemu-user-binfmt`)
- `mksquashfs` / `unsquashfs` (`squashfs-tools`)
- `mkfs.jffs2` / `mkfs.ubifs` / `ubinize` / `jffs2dump` (`mtd-utils`)
- `binwalk`, `file`, `binutils` (`readelf`, `strings`)

## Build

```bash
bash build_fw.sh all        # writes build/syn-ecu-v{1,2}.squashfs
```

`v1` and `v2` deliberately differ so they drive the diff exercise:

| Item | v1 | v2 |
|---|---|---|
| program version string | `SYN-ECU v1.0` | `SYN-ECU v2.0` |
| `admin_password` | `DemoPass!2024` | `DemoPass!2025` |
| `api_token` | `syn_token_aaa_1111` | `syn_token_bbb_2222` |
| `ipaddr` | `192.168.10.10` | `192.168.10.11` |
| `feature.conf` | absent | added (`secure_boot=on`) |

These are obviously fake markers for the credential/change-detection
exercise — never treat them as real secrets.

## Analyze

```bash
bash analyze.sh v1
bash analyze.sh v2
```

Expected results (proven 2026-09-21):

- binwalk identifies `Squashfs filesystem ... compression:gzip`
- extraction yields `/etc/{passwd,shadow,banner,inittab,init.d/rcS}`,
  `/etc/syn_ecu/{config.ini,network.conf}` and `/usr/bin/ecu-demo`
- ELF: `ELF 64-bit LSB executable, ARM aarch64, statically linked`
- credential hunt finds the fake `admin_password` / `api_token` in
  `config.ini`, the teaching MD5 placeholder in `shadow`, and the
  `SYN-ECU` / `UDS` / `vcan0` markers inside the ELF
- SUID audit reports **0** SUID files; `shadow` is mode `0600`
- `qemu-aarch64 usr/bin/ecu-demo` prints the boot banner and exits 0

> Extract as **root** if you need original uid/gid preserved; a non-root
> `unsquashfs` maps ownership to the current user but keeps permission bits.

## Firmware diff

```bash
bash firmware_diff.sh
```

Reports file-level changes (`diff -rq`), unified config/network diffs, the
embedded program version-string change, and a credential-change summary.
This is the workflow used to track what a vendor changes between revisions.

## JFFS2 / UBIFS / UBI

```bash
bash fs_formats.sh
```

- `mkfs.jffs2` → binwalk identifies `JFFS2 filesystem, little endian`
- `mkfs.ubifs` → binwalk identifies UBIFS superblock / master nodes
- `ubinize` → binwalk identifies the UBI erase-count header

Creation and identification are fully covered. Userspace **extraction** of
JFFS2/UBIFS on WSL2 (no real MTD devices) needs optional third-party tools:

- JFFS2: `jefferson` (pulls in a native `python-lzo` dependency)
- UBI/UBIFS: `ubireader` (`ubireader_extract_files`)

SquashFS extraction is fully covered by `unsquashfs` and is the primary
path; the JFFS2/UBIFS unpackers are a documented optional extension.

## Bridge to real firmware

When a legally obtained, authorized real sample is available, place it under
`labs/firmware/samples/` following `../samples/README.md`, then run the same
identification → extraction → inventory → ELF → strings → permission →
emulation steps. Do not commit real vendor images or private material.
