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

### [x] M9 — read an arbitrary sector
Expose `blk_read(lba, buffer)` and read a sector whose contents are not zero.
**Passed:** `virtio: sector2048 fnv=0xe7b1b310`, matching the host's hash of that sector.

### [ ] M13 — panic handler instead of triple fault
*Renumbered from M10 on 2026-11-15: the raw record store below was built first and took
the M10 slot. Numbers follow the order the work happened, not the order it was imagined.*
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

### [x] M10 — raw record store, no partition, no filesystem
Append-only records written straight to the bare disk: a superblock commit count, a
per-record FNV-1a, and tolerance for a record written but never committed. No partition
table, no filesystem, nothing between the request and the device. Full format, layouts and
pass condition in `M10-RAWSTORE.md`.

**Pass:** all eleven checks present in the serial log and agreed by `verify.py`, including a
negative test in which the host corrupts one byte inside a committed record and the guest
must report it rather than pass silently.

**Passed 2026-11-15:** `test.bat` reaches milestone 10 in ~6 s. Region hash
`0x50370dfe` matched the host's independent computation; a deliberately corrupted record was
detected and repaired; a record written but never committed was ignored; replay reproduced
identical bytes. Reference verifier: `py tools\rawstore_check.py --selftest`.

**First run disagreed with the host** — `0xbf8c9bfe` in the guest against `0x50370dfe` on
the host — because `format` left the reserved sectors holding the previous disk contents.
Fixed by zeroing them. A self-check would have agreed with itself.

### [x] M14 — boot from a disk that has it (geometry-independent boot)
`stage1.asm` reads the kernel with floppy CHS arithmetic (18 sectors/track, 2 heads), so the
same bytes cannot boot from a USB stick or a hard disk. Replace it with INT 13h LBA reads
(AH=42h), keeping the CHS loop as a fallback for any drive whose BIOS lacks extensions.
Emit `L` or `C` to COM1 so the path taken is observable in the log rather than claimed.
Full design, packet layout and pass condition in `M14-BOOT.md`.

**Pass:** floppy boot still reaches milestone 10 with unchanged hashes; the same image
attached as USB mass storage and as a hard disk also reaches milestone 10, with the store
region hash identical across all three; marker `L` present on both non-floppy runs; a medium
truncated to 16 sectors produces a logged disk error and no hang.

**Why it matters:** without it, ANVIL cannot leave the floppy it was born on, and no
installer is possible.

**No USB hardware required.** QEMU presents a file as a USB mass storage device, which tests
the thing that is actually in question — geometry independence. Real firmware compatibility
is a separate, parked concern.

**Passed 2026-11-15:** floppy boot reports marker `C` (SeaBIOS offers no INT 13h extensions
for a floppy) and reaches milestone 10; the same image as USB mass storage reports `L` and
reaches milestone 10 with an identical `store: region fnv=0x50370dfe`. Truncating the medium
to 16 KB produces `ANVIL: disk read failed` and a clean halt. At 32 KB the overrun read
*succeeded with zeros* instead of failing - which is why reads are bounds-checked against
capacity rather than trusting the device to complain.
### [x] M16 — let the policy be replaced, not the mechanisms
The kernel owns the mechanisms and the measurements; the decision layer is a 16-byte table on
disk, so better judgement improves the machine without a rebuild. Invariant: policy may change
cost, never correctness. Full spec in `M16-POLICY.md`.

**Pass:** two policy tables, one unchanged suite. Both pass every existing check, `store:
region fnv` is identical under both, and the larger chunk reports strictly fewer sectors moved.
A corrupt or missing policy table falls back to defaults and still passes.

**Why:** charter directives 3, 4 and 5 — an AI that directs the hardware, and does it better as
it improves. "Let the AI manage the machine" is unfalsifiable; "two policies, identical bytes,
fewer sectors" is a claim that can be wrong, which is what makes it worth building.

**Ordering note:** M11 (compression policy) moves ahead of the panic handler. Reliability
matters, but efficiency is directive 4 and it is measurable.

**Passed 2026-11-15:** `py tools\policy_test.py` runs three policies against one unchanged
suite. All three pass the full verifier with an identical region hash `0x50370dfe`;
`flush=16` moves 292 sectors against 352 for the defaults, and `chunk=4` issues 104 requests
against 352. Byte-identical output, 17% fewer sectors, 70% fewer requests.

Two defects found while building it: the policy table had to move out of the hashed region
(at LBA 1 every policy changed the hash), and `format`'s writes were not being counted, which
under-reported the baseline as 322 instead of 352.
### [ ] M17 — close the loop: propose a policy, let the verifier judge it
M16 built the seam but a script still writes the policy by hand, so "better judgement produces
a better machine" has been made possible, never tested. This closes the loop: measure baseline,
propose, run, and keep a proposal only if the output is byte-identical and the cost is strictly
lower. The proposer can be a grid search today and a model tomorrow - the kernel cannot tell
the difference, which is the point. Spec in `M17-LOOP.md`.

**Pass:** baseline recorded; at least one proposal accepted with an identical region hash and
strictly lower sectors or requests; every accepted policy re-verified; a deliberately bad
proposal REJECTED (a loop that only accepts is a rubber stamp); repeatable across two runs;
decisions recorded append-only outside the kernel.

### [x] M18 — know the size of what you manage (64-bit capacity and LBA)
The kernel kept only the low dword of the device's sector count, so a 3 TiB device was
described as ~1 TiB and every LBA past that boundary was refused - with the entire suite
passing, because every test worked near the start of the disk. Capacity is now 64-bit, all
bounds checks widened, and `store_probe_last_sector()` reads LBA capacity-1 and reports its
hash so the 64-bit path cannot regress unnoticed. Spec and results in `M18-LARGE-MEDIA.md`.

**Passed 2026-11-15:** 3 TiB device reports 6,442,450,944 sectors (lo=2147483648 hi=1); last
sector read at LBA 6,442,450,943 with fnv 0x1c9cd7c5, identical to the host's own FNV of the
pattern it wrote there with qemu-io. 8 MiB suite, USB boot and the policy comparison all still
pass. Two checks print [SKIP] rather than quietly passing, because the device is not testdisk.img.
