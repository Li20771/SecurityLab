# AFL++ Generic Fuzzing Lab

> **SYNTHETIC TARGET.** `vuln.c` is a deliberately vulnerable teaching
> program. It contains no real product code and no real CVE. It exists to
> prove the local AFL++ workflow: instrumented build → seed corpus → fuzz →
> crash discovery → deterministic reproduction.

## What is verified

- AFL++ installed and working on WSL2 Ubuntu (`afl-fuzz`, `afl-gcc`)
- compile-time instrumentation
- a short fuzz campaign discovers a memory-safety crash
- the saved crash reproduces deterministically (same signal on repeat)
- ASan build gives a precise `stack-buffer-overflow` report

## Toolchain note (important on this machine)

Use the **GCC** wrappers (`afl-gcc` / `afl-g++`), not `afl-clang-fast`:

- the distro `afl-clang-fast` LLVM pass (`SanitizerCoveragePCGUARD.so`) is
  built against **LLVM 17**, while the system default clang is **18**;
  loading the pass under clang-18 fails with an undefined LLVM symbol.
- `afl-gcc` has no LLVM version coupling and the ASan build uses GCC's own
  `libasan`. Both instrumented binaries build and run correctly.

To use the clang path later, install a matching `clang-17` +
`libclang-rt-17-dev` and export `AFL_CC=clang-17 AFL_CXX=clang++-17`.

## Files

```
afl-demo/
├── README.md
├── vuln.c      synthetic vulnerable parser (stack overflow)
├── build.sh    afl-gcc instrumented builds (ASan + plain) into /tmp/afl-demo
└── run.sh      build -> seeds -> time-boxed fuzz -> triage -> repro
```

Campaign work happens under `/tmp/afl-demo` (WSL native fs) because AFL++
shared memory/locking and throughput are poor on the `/mnt/d` drvfs mount.

## Run

```bash
bash run.sh 60          # 60-second campaign (default)
```

Expected (proven 2026-09-21, crash found within ~135 execs):

- crash file under `/tmp/afl-demo/out/default/crashes/`, e.g.
  `id:...,sig:06,...` (signal 6 = SIGABRT from ASan)
- replay exit code `134` (128+6) on both runs
- `ERROR: AddressSanitizer: stack-buffer-overflow`
- final line `RESULT=PASS crash discovered and deterministically reproduced`

## The bug

`vuln.c` treats input byte `[3]` as a payload length and `memcpy`s that many
bytes into a 16-byte stack buffer with no bounds check. A seed beginning with
the magic `AFL` is enough for AFL++ to mutate the length byte past 16 and
trigger the overflow.

## Extending

- point the same harness pattern at a real parser you are authorized to test
- add a dictionary (`-x`), parallel jobs (`-M main -S child`), and a longer
  time budget for real campaigns
- keep long/heavy fuzzing out of fast CI; gate it behind an explicit
  "extended security test" job
