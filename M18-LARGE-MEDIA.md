# M18 — know the size of what you manage

**Status:** implemented, passing.
**Depends on:** M10 (store), M14 (boot paths).
**Found by:** a 3 TiB device on `E:`, which nothing in the suite had ever tested.

## The defect

The kernel read the device's capacity as two 32-bit halves, printed both, and then kept only
the low one:

```c
g_capacity_sectors = cap_lo;      /* 32-bit */
u32 blk_capacity_sectors(void)
```

On a 3 TiB device (6,442,450,944 sectors) that stores 2,147,483,648 — the low dword, wrapped.
The kernel believed the disk was 1 TiB and refused every LBA beyond it as out of range.

**And the entire test suite passed.** Every check green, honest hashes, replay identical — on
a device it had misdescribed. That is the important part of this milestone: it was not a logic
failure but a *coverage* failure. Every existing test works near the start of the disk, so a
32-bit sector count could describe a 3 TiB device as a third of its real size and nothing
noticed.

## The fix

- capacity is `u64`, assembled from both halves: `((u64)hi << 32) | lo`
- every bounds check in `blk_read_sector`, `blk_write_sector` and `blk_write_run` widened
- the capacity line now reports both halves (`virtio: capacity lo=… hi=…`) instead of a
  misleading single number
- the store no longer prints a device size at all — it prints the slots it formatted, which is
  the only quantity it is actually entitled to state

## The new check

`store_probe_last_sector()` reads the device's **last** sector and reports the LBA and its
hash. A read at `capacity - 1` cannot pass unless the whole 64-bit path works, so this closes
the coverage hole that let the bug through.

The host writes a known pattern into that sector with `qemu-io` before boot, so the expected
value comes from outside the guest:

```
qemu-io -f qcow2 -c "write -P 0xAB <offset> 512" E:\_aos_bigmedia.qcow2
```

## Result

```
virtio: capacity lo=2147483648 hi=1
store: last sector lba_lo=2147483647 lba_hi=1 fnv=0x1c9cd7c5
```

| Check | Result |
|---|---|
| capacity reported | 6,442,450,944 sectors (3.00 TiB) — matches the host |
| last sector LBA | 6,442,450,943 — past the 2³² boundary |
| last sector content | `0x1c9cd7c5`, identical to the host's own FNV of the pattern |
| 8 MiB test disk, full suite | PASS, unchanged |
| USB boot | PASS, marker `L`, same region hash |
| policy comparison | PASS, same bytes, fewer sectors |

Two checks are reported as **[SKIP]**, not quietly omitted: the sector-0 and LBA-2048
comparisons compare against `images/testdisk.img`, which is meaningless for a different
device. A skipped check has to be visible, or the log claims more than it verified.

## Sandbox rule amendment

Large-media work needs scratch space bigger than `D:` can usefully hold, so **`E:` is now an
approved scratch location for file-backed test images only.** Specifically:

- a sparse image file on a filesystem, attached to QEMU as a device — nothing else
- **never** the raw device: no `\\.\PhysicalDriveN`, no formatting, no partitioning
- the 3 TiB test device is 240 KB on disk, because qcow2 is sparse

A test that needs 3 TiB of *real* space would be a different decision, and not one this
milestone requires.

## Why this belongs to the thesis

"Hardware improves slowly" is only reassuring if you keep up with it. A 32-bit sector count
was correct for a 1.44 MB floppy and silently wrong for a 5 TB drive, and the failure was
invisible because the tests were written when disks were small. A system that manages scarce
resources has to know the true size of what it manages — and has to have a test that fails
when it doesn't.
