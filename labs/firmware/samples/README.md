# Firmware Sample Management

This directory holds **real, legally obtained firmware samples** for analysis.
It ships empty (no binaries) because SecurityLab must not redistribute vendor
images or private/unauthorized material.

## Legal / handling rules

1. Only store firmware you are **authorized** to possess and analyze
   (vendor download with an EULA that permits research, a CTF/practice image,
   your own device dump, or an explicitly licensed sample).
2. **Never commit** vendor binaries, proprietary images, or anything containing
   third-party copyrighted code / private keys. `images/` is git-ignored.
3. Record provenance and a hash for every sample in a local manifest (see
   `MANIFEST.template.yml`). Keep the manifest local if it is sensitive.
4. Prefer the synthetic targets in `../synthetic-ecu/` for repeatable,
   shareable exercises; use real samples only for genuine research.

## Layout

```
samples/
├── README.md                 this file (tracked)
├── MANIFEST.template.yml     copy to MANIFEST.local.yml per sample (local only)
└── images/                   actual binaries (git-ignored, kept on disk only)
    └── .gitkeep
```

## Adding a sample

```bash
cp /path/to/firmware.bin images/<vendor>-<device>-<version>.bin
sha256sum images/<vendor>-<device>-<version>.bin
cp MANIFEST.template.yml MANIFEST.local.yml   # document source / license / hash
```

Then run the standard pipeline (no special tooling required):

```bash
file   images/<sample>
binwalk images/<sample>
binwalk -e --run-as=root -C /tmp/extract images/<sample>   # as root
# or, for squashfs:  unsquashfs -d /tmp/root images/<sample>
```

## What to capture during triage

- container / filesystem signatures (binwalk)
- architecture of every ELF (`file`, `readelf -h`)
- `/etc` config, init scripts, network configuration
- hardcoded credentials / keys / tokens (`strings`, `grep`)
- SUID/SGID binaries and capabilities
- web services / listening ports / startup daemons
- version strings (for later firmware-diff against a newer revision)

## Supported formats in this environment

| Format | Build | Identify | Extract |
|---|---|---|---|
| SquashFS | mksquashfs | binwalk / file | **unsquashfs (full)** |
| JFFS2 | mkfs.jffs2 | binwalk / jffs2dump | jefferson (optional) |
| UBIFS | mkfs.ubifs | binwalk | ubireader (optional) |
| UBI | ubinize | binwalk | ubireader (optional) |

A blank sample set is the intended, honest state until an authorized real
sample is available — do not fabricate one and label it as real.
