# M10 — raw record store, no partition, no filesystem

**Status:** implemented, passing. Milestone 10.
**Depends on:** M9 (arbitrary-LBA read), virtio-blk read/write path.
**Sandbox:** unchanged. `run_vm.bat` with `-snapshot`, file-backed images, no NIC.

## The question this answers

Can the kernel store bulk data on a bare disk — no partition table, no filesystem — and
get it back intact? Yes, and this milestone is how that claim becomes checkable rather than
merely asserted.

The disk has no opinion about partitions. What a partition table actually provides is a
*convention for finding things*, so if we skip it we must supply our own. That convention
is the whole of this milestone.

## Design principle: the verifier exists before the code

A checker written after the implementation tends to agree with it. So the reference
implementation and the format land first (`tools/rawstore_check.py`), and the kernel is
written to satisfy them. Two independent implementations — C in the guest, Python on the
host — must agree byte for byte. When they disagree, one of them is wrong, and the
disagreement is the finding.

## On-disk layout

Fixed 512-byte sectors, 8 MiB image (16,384 sectors), as `mkdisk.py` already builds.

| Region | Sectors | Contents |
|---|---|---|
| Superblock | 0 | commit record — the only authority on how many records exist |
| Reserved | 1–15 | held for future metadata; `format` zeroes it so the region hash does not depend on what was on the disk beforehand |
| Records | 16 → 16,383 | 4,092 fixed-size records, 4 sectors (2,048 bytes) each |

A record never spans a boundary and is never moved. Record `i` starts at
`LBA = 16 + i * 4`, which removes the need for an index entirely: the count in the
superblock is sufficient to locate everything. Fewer moving parts, fewer things to get wrong.

Capacity is `(16384 - 16) / 4 = 4092` records, which is 8.25 MB of payload — the whole
test disk, with no space spent on a filesystem.

### Superblock (sector 0, little-endian)

| Offset | Size | Field |
|---|---|---|
| 0x00 | 8 | magic `ANVILRS1` |
| 0x08 | 4 | format version, `1` |
| 0x0C | 4 | record size in sectors, `4` |
| 0x10 | 4 | data start LBA, `16` |
| 0x14 | 4 | **committed record count** |
| 0x18 | 4 | FNV-1a over bytes 0x00–0x17 |
| 0x1C | 484 | zero |

### Record header (first 32 bytes of the record)

| Offset | Size | Field |
|---|---|---|
| 0x00 | 4 | magic `REC1` |
| 0x04 | 4 | record index, must equal its position |
| 0x08 | 4 | payload length in bytes, ≤ 2,016 |
| 0x0C | 4 | FNV-1a of the payload |
| 0x10 | 4 | sequence number, strictly increasing |
| 0x14 | 4 | FNV-1a over header bytes 0x00–0x13 |
| 0x18 | 8 | zero |

### Commit protocol, and why it matters

Append writes the record **first**, then rewrites the superblock count. The count is the
only commit point. Power loss between the two leaves a fully written record that is not
committed: on restart the kernel trusts the count, ignores the orphan, and appends over it.
That gives crash consistency without a journal, at the cost of one sector rewrite per append.

## Payload generator — identical in both languages

Both implementations fill payloads from the same 32-bit LCG, so outputs are byte-identical
and any divergence is a real defect rather than a formatting difference:

```
state = 0x12345678 + index
repeat payload_length times:
    state = (state * 1103515245 + 12345) & 0xFFFFFFFF
    emit (state >> 16) & 0xFF
```

## API the kernel must expose

```c
int  store_init(void);                  /* read superblock, validate, or format */
int  store_append(const void *data, u32 len);   /* returns index, or -1 */
int  store_count(void);                 /* committed records */
int  store_read(u32 index, void *out, u32 *len);
int  store_verify_all(void);            /* recompute every record, report first mismatch */
```

## Pass condition

The test passes only when **every** line below is present in the serial log and
`verify.py` agrees with the host-computed values. No partial credit.

| # | Check | Expected |
|---|---|---|
| 1 | Format on a blank image | `store: formatted 16384 sectors, 4088 slots` |
| 2 | Append 64 records | `store: appended 64, count=64` |
| 3 | Read back record 0 | `store: rec0 fnv=0x…` == host FNV of record 0 |
| 4 | Read back record 63 | matches host |
| 5 | Verify all | `store: verify ok 64/64` |
| 6 | Superblock FNV | matches host |
| 7 | Orphan record (written, not committed) | count stays 64; `store: orphan ignored` |
| 8 | Corrupted payload, host-flipped byte | `store: verify FAIL rec 32` — must not pass silently |
| 9 | Read beyond count | refused with `store: out of range 64` |
| 10 | Fill to capacity, append once more | last append refused with `store: full 4092` |
| 11 | Re-run after full cycle | identical hashes, byte-identical image |

Check 8 requires a **negative** test: the host corrupts a byte inside a committed record,
the guest must detect it. A verifier that cannot fail is not a verifier — the same reason
`bank.py --verify` is tested by appending a byte.

## What this does not prove

- **Durability under real power loss.** Only that the commit ordering is correct by
  construction; QEMU's `-snapshot` discards writes, so no test here survives an exit.
- **Anything about physical hardware.** Still one virtual device, still one queue.
- **Repair.** FNV detects corruption; it does not correct it. Redundancy or ECC would be a
  separate milestone.
- **Performance.** This is a correctness milestone. `io_bench` already owns the numbers, and
  its finding stands: writing fewer bytes beats writing faster.

## Result

`test.bat` reaches milestone 10 in about six seconds:

```
store: formatted 16384 sectors, 4092 slots
store: appended 64, count=64
store: rec0 fnv=0x23f9753d         host expects 0x23f9753d
store: rec63 fnv=0x2976596b        host expects 0x2976596b
store: verify ok 64/64
store: superblock fnv=0x661fc21c   host expects 0x661fc21c
store: region fnv=0x50370dfe       host expects 0x50370dfe
store: orphan ignored
store: verify FAIL rec 32          deliberate corruption, detected
store: verify ok 64/64             and repaired
store: out of range 64
store: full 4092
store: replay identical
```

### The defect this design caught

The first run disagreed with the host: the guest reported `0xbf8c9bfe` for the region, the
host `0x50370dfe`.

Cause: `format` wrote the superblock but left the reserved sectors 1–15 holding whatever
was already on the disk — the previous testdisk pattern. The region hash therefore depended
on the disk's history rather than on the formatter, which is precisely what a formatter is
supposed to eliminate. Fixed by zeroing the reserved area as part of `format`.

A self-check would have agreed with itself and shipped. Two independent implementations
did not.

### The verifier was proved able to fail

- tampering the region hash in the log → `RESULT: FAIL`
- deleting the `verify FAIL rec 32` line → `RESULT: FAIL`

### Side effect

The kernel outgrew its 16 KB load area, so `KERNEL_SECTORS` in `stage1.asm` and
`KERNEL_LIMIT` in `mkimage.py` moved together to 64 sectors (32 KB). Those two numbers must
always change as a pair; the comment in each says so.

## Order of work

1. `tools/rawstore_check.py` — build, verify, corrupt, prove the corruption is detected.
   **Done.**
2. `kernel/store.c` — format, append, read, verify, on the existing virtio-blk path.
   **Done.**
3. Wired into `kernel.c` and checked by `verify.py`. **Done, milestone 10.**
4. `0.1.2` — once the wording in `0.1.1` is settled.
