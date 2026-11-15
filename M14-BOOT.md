# M14 — boot from a disk that has it (geometry-independent boot)

**Status:** implemented, passing. Both boot paths verified.
**Depends on:** M10 (the kernel works, and is verified by hash). Nothing else.
**Sandbox:** unchanged in principle — but see the one deliberate exception below.

## The problem this answers

The chicken and the egg, honestly stated: on a blank disk, the first write always comes
from outside the disk. That is true of every operating system — Linux arrives via `dd` or
Rufus, Windows via a USB stick it did not make, servers via PXE, and every BIOS machine via
firmware held in ROM, which is never blank.

So the boot medium is not the difficulty. The difficulty is this line, in `stage1.asm`:

```asm
; LBA = index + 1 -> CHS (18 sectors/track, 2 heads)
```

**That is floppy geometry, hardcoded.** Put those same bytes on a USB stick or a hard disk
and the CHS arithmetic names different physical places: it reads the wrong sectors and dies.
ANVIL cannot currently leave the floppy it was born on.

## The change

Replace the CHS read loop with **INT 13h extensions (AH=42h)**, which address by LBA and do
not care whether the device is a floppy, a stick or a disk.

### Disk Address Packet (16 bytes, at a fixed offset in the boot sector)

| Offset | Size | Field |
|---|---|---|
| 0x00 | 1 | packet size, 16 |
| 0x01 | 1 | reserved, 0 |
| 0x02 | 2 | sector count, 1..127 |
| 0x04 | 2 | buffer offset |
| 0x06 | 2 | buffer segment |
| 0x08 | 8 | starting LBA |

Call: `AH=42h`, `DL=`drive, `DS:SI`=packet, `INT 13h`. Carry set means failure; retry three
times before giving up, because USB media are slower to settle than a floppy.

### Reading 64 sectors without crossing a segment

A transfer must not cross a 64 KiB boundary, so 32 KB is read in **four chunks of 16
sectors** (8 KB each) at segment `0x1000`, `0x1200`, `0x1400`, `0x1600` — covering
`0x10000`–`0x18000`. Each chunk sits inside one segment window and none crosses `0x20000`.

### Fallback, because a floppy may not support extensions

Not every BIOS offers extended reads for a floppy drive. So support is *detected* first:

```
AH=41h, BX=0x55AA, DL=drive, INT 13h
supported  ->  CF clear and BX == 0xAA55
```

If supported: read by LBA. If not: keep the existing CHS loop untouched. This is not
sentimentality about floppy drives — it is what keeps the regression test meaningful, and it
means one image boots on both.

### The boot path must be observable

A boot loader that claims to have taken the LBA path is worth nothing. So `stage1` writes a
single character to **COM1 (port 0x3F8)** before jumping:

| Marker | Meaning |
|---|---|
| `L` | extended LBA reads were available and used |
| `C` | extensions unavailable, fell back to CHS |

`verify.py` then asserts which path was taken, per configuration. The letters already appear
at the head of every existing log (`A`, `B`, `C`, …), so this fits the existing evidence
style rather than inventing a new one.

## Test configurations

| Config | How the image is attached | Path it must take |
|---|---|---|
| Floppy (regression) | `-drive if=floppy,file=anvil.img` | `L` if supported, else `C` — recorded, not assumed |
| USB stick | `-device qemu-xhci -drive id=u,if=none,file=anvil.img -device usb-storage,drive=u` | `L` |
| Hard disk | `-drive format=raw,if=ide,file=anvil.img` | `L` |

A new `run_vm_usb.bat` takes a label and attaches the image as USB mass storage; `run_vm.bat`
keeps doing what it does today.

## Pass condition

No partial credit. Every line must be in the serial log and agreed by `verify.py`.

| # | Check | Expected |
|---|---|---|
| 1 | Floppy still boots | milestone 10, `RESULT: PASS`, unchanged hashes |
| 2 | Boot path marker present | `L` or `C` as the first character of the log |
| 3 | USB-attached image boots | milestone 10, same store hashes as the floppy run |
| 4 | USB run reports | marker `L`, `store: region fnv=0x50370dfe` |
| 5 | Hard-disk-attached image boots | milestone 10 |
| 6 | Same kernel either way | `store: region fnv` identical across all three configurations |
| 7 | Negative test | a boot medium truncated to 16 sectors fails with a logged disk error, not a hang |

Check 7 matters for the same reason as M10's corruption test: a loader that cannot fail is
not a loader. It must say "disk read failed", not spin forever.

## The one deliberate sandbox exception

`-snapshot` discards guest writes, which is what makes two-boot testing impossible — an
installed disk vanishes when the VM exits. This milestone does **not** need to write:

- Boots from USB/disk are read-only against the image.
- The image itself is built on the host by `mkimage.py`, as now.

So `-snapshot` stays on, and no exception is required for M14. The exception becomes
necessary only in **M15 (self-install)**, where the guest writes to a target disk and must
reboot from it. That trade-off is named here so it cannot arrive as a surprise later: M15
gets one throwaway 8 MiB image, no snapshot, and a documented reason.

## What this does not prove

- **Real firmware.** QEMU's USB emulation proves the boot code no longer depends on floppy
  geometry. It does not prove compatibility with a particular motherboard's BIOS.
- **UEFI.** Modern machines often ship with CSM disabled or absent, in which case a legacy
  boot sector never runs at all. UEFI needs a FAT32 ESP and a PE/COFF `.efi` binary — a
  separate project, not a tweak.
- **Usability after boot.** Still serial only, still virtio-only for storage, no display, no
  keyboard, no network. M14 proves the *chain*, not the ecosystem.

## Result

| Configuration | Marker | Milestone | `store: region fnv` |
|---|---|---|---|
| Floppy (`run_vm.bat`) | `C` — CHS fallback | 10 | `0x50370dfe` |
| USB mass storage (`run_vm_usb.bat`) | `L` — extended LBA | 10 | `0x50370dfe` |

Same kernel, same hashes, two completely different loading paths. `verify.py` asserts the
marker on every run and takes `--require-lba` for the configurations where falling back
must count as a failure.

SeaBIOS offers no INT 13h extensions for a floppy, which is exactly why the fallback is
not sentimentality: without it the regression path would die the moment the LBA path was
added.

### What the truncated-image test found

Three sizes were tried, and the middle one is the useful one:

| Truncated to | Behaviour |
|---|---|
| 16 sectors (8 KB) | the firmware rejects the device outright - the boot sector never runs, log empty |
| 32 sectors (16 KB) | boots, reports `L`, then `ANVIL: disk read failed` and halts cleanly |
| 64 sectors (32 KB) | boots, reports `L`, and the read past the end **succeeded with zeros** |

The third row is the finding worth keeping. A read past the end of a USB mass storage
device can return zeros rather than reporting failure, so a loader that trusts every read
will happily jump into a 32 KB block of zeroes. That is precisely why `blk_read_sector()`
bounds-checks against the reported capacity instead of trusting the device to complain.

### Order of work

1. Support probe and LBA path in `stage1.asm`, CHS kept as fallback, `L`/`C` emitted to
   COM1. **Done.**
2. `run_vm_usb.bat` with `bootindex=0` so the result does not depend on enumeration order.
   **Done.**
3. `verify.py` asserts the marker, and `--require-lba` for strict configurations. **Done.**
4. Truncated-image negative test. **Done, and it taught us something.**
5. M15 (self-install) is now unblocked.
