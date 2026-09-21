# YARA / IOC Basics Lab

> **SYNTHETIC indicators only.** The rules and markers in this lab are
> hand-made teaching material, not signatures of real malware.

## Goal

Prove the local YARA workflow end to end:

```
author rule (.yar) -> yara compile -> scan samples -> TP/TN result
```

and show how IOC scanning also applies to firmware/ELF analysis.

## Layout

```
yara-basic/
├── README.md
├── rules/demo.yar          two synthetic rules (text + hex + regex)
├── samples/
│   ├── sample_hit.txt      contains the synthetic beacon markers   (must alert)
│   ├── sample_ecu.txt      contains SYN-ECU/UDS/version markers     (must alert)
│   └── sample_clean.txt    benign text                             (must NOT alert)
└── scan.sh                 compiles, scans, asserts TP / TN
```

## Rules

- `SecurityLab_Synthetic_Beacon` — ASCII marker `SYNMALWARE_C2_BEACON` and a
  fake C2 URL (`any of them`).
- `SecurityLab_Synthetic_EcuFirmware` — hex `{41 46 4C}` ("AFL"), the
  `SYN-ECU` banner, a UDS string, and a `/SYN-ECU v[0-9]+\.[0-9]+/` version
  regex (`2 of them`, reducing false positives).

## Run

```bash
bash scan.sh
```

Expected (proven 2026-09-21, YARA 4.5.0):

```
TRUE_POSITIVE(beacon) = PASS
TRUE_POSITIVE(ecu)    = PASS
TRUE_NEGATIVE(clean)  = PASS
YARA_RESULT=PASS
```

If the synthetic ECU firmware lab has been built/extracted, `scan.sh` also
runs the rules against the static AArch64 `ecu-demo` ELF to demonstrate IOC
scanning inside firmware binaries.

## Next steps for real work

- maintain one rule file per threat/family with `meta` provenance
- combine strings, hex patterns and regex; require multiple indicators
- test every rule against known-good data to control false positives
- version rules and keep a corpus of known-positive samples locally
  (do not commit real malware; follow a handling policy)
