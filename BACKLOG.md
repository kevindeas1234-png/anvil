# ANVIL backlog

Paste-ready issues. Each one has a pass condition, because a ticket without one
is just a wish. `[x]` marks work already done in this tree.

## ANVIL kernel

### [x] M0-M6 — boot, protected mode, paging, interrupts, heap, serial shell
Boot sector → GDT → 32-bit → IDT/PIC/PIT → identity map → heap → shell.
**Pass:** `test.bat` reaches `AOS TICK TEST COMPLETE` and catches a deliberate `#UD`.

### [x] M7 — virtio-blk read
Legacy virtio-pci, 256-entry ring, read sector 0, FNV-1a hash.
**Pass:** kernel hash equals the host's hash of `images\testdisk.img` (`0x206ce5e2`).

### [x] M8 — virtio-blk write and read-back
Write a known pattern to sector 1, clear the buffer, read it back, compare.
**Passed:** `sector0 fnv=0x206ce5e2` and `writeback fnv=0xe161b9c5 roundtrip ok`, both matching host-computed hashes (2026-11-12).

### [ ] M9 — read an arbitrary sector
Expose `blk_read(lba, buffer)` and read a sector whose contents are not zero.
**Pass:** hash of sector 2048 matches the host's hash of that sector.

### [ ] M10 — panic handler instead of triple fault
On any exception, dump vector, error code, EIP, and the last N log lines, then halt cleanly.
**Pass:** an intentional null dereference prints a panic block and does not reset.

### [ ] M11 — run the io_bench policy inside ANVIL
Sample the first 4 MiB, compress, choose plain vs packed, write, verify.
**Pass:** 256 MiB written as ~4 MiB with a matching round-trip hash.

### [ ] M12 — recovery path for a worn disk image
Retry a failed request, reset the queue, log the failure.
**Pass:** a deliberately truncated image produces a logged error, not a hang.

## io_bench

### [x] Baseline harness
Five data paths, hashed outputs, numbered runs, `reproduce.bat`.

### [x] Findings with negative control
Queue depth: no effect. In-place: worse. Compression: 3.6x when it pays, 0.17x when it doesn't.

### [x] `choose.py` policy
Probe 4 MiB, decide, write, verify.

### [ ] M13 — access planner
`planner.py`: probe device + data, choose path/chunk/depth/compression, execute, verify.
**Pass:** HDD picks sequential depth-1 4 MiB and verifies; NVMe picks 1 MiB depth 16; text payload packs.
**Status:** implemented and self-tested (in-memory FS); awaiting real-media runs. Depth is recommended but execution is depth-1 only.

### [ ] Sustained-state run
1 GiB on both drives to expose the SLC-cache cliff.
**Pass:** a reported figure that is lower than the 256 MiB burst number, on both media.

### [ ] Publish
`git init`, remote, and a short write-up: "write less, not faster".
**Pass:** a public URL where a stranger can run `reproduce.bat`.

## Tooling

### [x] Skills
`anvil-kernel`, `io-bench`, `aos-audit` installed globally.

### [x] Source snapshots
`tools\history.py`; `test.bat` snapshots before every build.

### [ ] CI hook
Run `test.bat` from a scheduled task or post-commit hook.
**Pass:** a failing build produces a non-zero exit without human intervention.
