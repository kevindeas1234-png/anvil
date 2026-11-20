# ANVIL

A small 32-bit x86 kernel that boots from a floppy image in QEMU and talks to a
virtio disk. Every claim in this README is checked by a script against the kernel's
own serial output, and the hashes it reports are compared with hashes computed
independently on the host.

It is not a usable operating system. It is a working kernel with a test harness that
refuses to flatter it.

```
$ test.bat
milestone detected: 9
[PASS] 'ANVIL KERNEL ONLINE'
[PASS] 'e820: '
[PASS] 'paging: enabled'
[PASS] 'paging: remap verified'
[PASS] 'heap: alloc/free ok'
[PASS] ticks 1..10 in order
[PASS] 'fault test: raising #UD'
[PASS] 'exception 0x00000006'
[PASS] sector0 0x206ce5e2 (host expects 0x206ce5e2)
[PASS] writeback 0xe161b9c5 (host expects 0xe161b9c5)
[PASS] lba 2048: 0xe7b1b310 (host expects 0xe7b1b310)
RESULT: PASS
```

## Why this exists

Not to run faster for its own sake. The measurements in `io_bench` are the argument: on
the same disk, raising queue depth from 1 to 64 moved throughput from 56 to 75 MiB/s, while
deciding to write 4 MiB instead of 256 MiB was 3.6 times faster. The leverage was never in
pushing bytes harder — it was in the decision made above the data path.

So the thesis is narrow and testable: **a kernel that knows the workload can beat one that
merely moves bytes quickly.** ANVIL measures what it moved, ANVIL verifies what it wrote,
and the policy that decides *how* is meant to sit above the kernel rather than inside it —
so that better judgement improves the machine without a single line of driver code changing.

That is also why every claim here is checked by a script against a hash computed elsewhere.
A kernel whose performance depends on its own judgement had better be verifiable.

### The result so far

Policy is data, not code: a 16-byte table in the device's last sector decides chunk size and
commit cadence. Three policies, one unchanged test suite:

| Policy | Sectors moved | Requests | Committed region hash |
|---|---|---|---|
| defaults (chunk 1, flush 1) | 352 | 352 | `0x50370dfe` |
| chunk 1, flush 16 | **292** | 292 | `0x50370dfe` |
| chunk 4, flush 8 | 296 | **104** | `0x50370dfe` |

**Byte-identical output. 17% fewer sectors. 70% fewer requests.** Both runs still verify,
and the hash is the same one an independent host implementation computes.

The leverage is the decision, not the speed — which is the same lesson `io_bench` produced
from the other direction, and the reason `tools/policy_test.py` exists to check it rather
than a paragraph existing to assert it.

## What it actually does

The kernel is a flat binary loaded at `0x10000` by a two-stage boot sector. Boot path:

`stage1` (boot sector) -> protected mode -> `entry` (GDT, stack) -> `kernel.c`

| Done | Proof |
|---|---|
| Boot to protected mode | serial banner |
| GDT, IDT, PIC remap, PIT | ticks `1..10` in order |
| E820 memory map | `e820:` line with entry count |
| Paging, identity-mapped | `paging: remap verified` (writes through a remapped alias) |
| Kernel heap (1 MiB arena) | `heap: alloc/free ok` |
| Interrupt stubs for all vectors | deliberate `#UD` is caught, `err`/`eip` reported, execution continues |
| Serial shell | `ANVIL shell ready` |
| virtio-blk read | FNV-1a of sector 0 matches the host's hash of the image |
| virtio-blk write + read-back | pattern written to sector 1, buffer cleared, read back, hash matches |
| Arbitrary-LBA read | LBA 2048 hashed in the guest, compared with the host |
| Raw record store | 64 records on a bare disk with no partition table and no filesystem; the whole committed region hashed in the guest and matched against an independent host implementation |
| Faults explain themselves | vector by name, error code, and the last twelve lines printed — then a clean halt, never a silent reset |
| Pack or plain, decided by measurement | 1024 bytes of runs → 10 packed (512 written); noise stays plain at 1024. Both verified by FNV, and packing noise fails the suite |
| Boots from floppy **and** USB/disk | the same image boots from both; the boot sector prints `L` (extended LBA reads) or `C` (CHS fallback) to the serial log, so how it loaded is recorded, not assumed |

The point of the last four is that **the guest's claim and the host's measurement are
compared**. A kernel that prints "disk OK" proves nothing; one whose hash has to match a
value computed outside the VM is harder to fake and much easier to debug.

## Requirements

### To run it

| Requirement | Minimum | Verified how |
|---|---|---|
| Guest RAM | **4 MiB** | see below |
| Boot medium | ~33 KB (boot sector + 64 kernel sectors) | a 16 KB medium fails with `ANVIL: disk read failed` |
| Firmware | legacy BIOS with INT 13h, extensions preferred | `run_vm_usb.bat` uses them; the floppy path falls back to CHS |
| Disk (optional) | 8 MiB virtio-blk, for the record store test | `mkdisk.py` builds it |
| CPU | any x86 with protected mode and paging | nothing exotic assumed |

The memory figure is measured rather than estimated, because a guessed minimum is just a
wish with a number attached:

| Guest RAM | Result |
|---|---|
| 1 MiB | 6 checks fail |
| 2 MiB | 3 fail - the paging remap cannot verify itself |
| 3 MiB | 1 fails - no room for the 1 MiB heap arena above the 2 MiB mark |
| **4 MiB** | **all checks pass** |
| 256 MiB | the default, because the harness should not be the thing being tested |

### Explicitly not supported

- **UEFI.** Legacy BIOS only. Most machines built since 2020 can disable CSM or lack it
  entirely, in which case this boots nothing.
- **Any physical hardware.** One virtual device, one queue. There are no drivers for real
  chipsets, and `DRIVER-LIST.md` explains why one machine's inventory is not a plan.
- **More than one CPU, any network, any display, any input device.** Serial only.

## Running it

To build it, all free:

| Tool | Version used | Why |
|---|---|---|
| [NASM](https://www.nasm.us/) | 2.16.03 | stage1, entry, interrupt stubs |
| [Zig](https://ziglang.org/) | **0.13.0** | `zig cc` compiles the C and links; newer releases change flags |
| [QEMU](https://www.qemu.org/) | 11.1.0 | the only supported machine |
| Python | 3.10+ | `verify.py`, `tools/history.py` |

Place NASM in `tools\nasm\nasm.exe` and Zig in `tools\zig\zig.exe`, or put both on
`PATH`. Then:

```
build.bat        # assemble, compile, link, package images\anvil.img
run_vm.bat       # boot it headless with the serial log going to logs\
run_vm_usb.bat   # the same image, attached as a USB mass storage device
test.bat         # build + boot + verify, and print RESULT: PASS/FAIL
```

`test.bat` is the only command you need. It leaves the full serial log in `logs\`.

## The sandbox

The guest is given two **file-backed** drives and nothing else: no NIC, no display, no
USB, no host disk, and `-snapshot`, so guest writes are discarded. It runs as an ordinary
user process — no admin, no drivers installed.

That is verifiable rather than merely intended. Run a test in which the guest writes a
sector, then compare the image before and after:

```
SHA256 before:  3F91362899BD391C...  testdisk.img
guest log:      virtio: sector1 written
                virtio: writeback fnv=0xe161b9c5 roundtrip ok
SHA256 after:   3F91362899BD391C...  testdisk.img    <- unchanged
```

The write really happened inside the VM and left no trace on the host file.

## Scope

ANVIL works in sectors. There is no partition table parser, no concept of a mounted volume,
and no write protection: a write request goes to the disk the kernel was given.

In practice the harness keeps this bounded. `run_vm.bat` attaches two file-backed images and
passes `-snapshot`, so guest writes are discarded when the VM exits — the image hashes are
identical before and after a run in which the guest writes a sector.

Both of those are properties of the launcher rather than of the kernel, so they are the parts
to keep intact if you adapt the harness or point it at different hardware.

Published as-is; the licence terms in `LICENSE` apply. [`SECURITY.md`](SECURITY.md) lists the
exposures in full, and how to report a problem.

## What it deliberately does not do

Stated plainly, because a kernel README that implies more than it does wastes everyone's
time:

- **No real hardware.** Only QEMU's legacy virtio-pci. There are no drivers for any
  physical chipset, and no attempt at one. If you want to know what drivers *your* machine
  would need, compile the list yourself — [`DRIVER-LIST.md`](DRIVER-LIST.md) explains why
  one machine's driver inventory is a fingerprint rather than a plan, and how to build
  your own.
- **No userspace.** There are no processes, no `fork`, no syscalls, no privilege rings
  beyond ring 0.
- **One address space, 32-bit.** The identity map is fixed; there is no per-process
  virtual memory and no 64-bit support.
- **No filesystem, no network, no SMP.** One CPU, one flat disk address space, sectors.
- **Not safe.** Faults are caught and printed, but there is no panic path that preserves
  state, and a bad descriptor will still take the machine down.
- **It boots nowhere except a VM.** It is floppy-sized on purpose: everything has to fit
  in an image that is easy to hash and easy to throw away.

## How this was made

The code in this repository was generated by a locally-run language model, working through
LM Studio's Bionic agent harness — the instance used here is called **Hammer** — under my
direction. The scope, the hard constraints (sandbox only; no writes outside two file-backed
disk images), the acceptance tests, and the decision to publish were mine. Where I delegated a
decision to the agent, the reasoning is written down rather than left implicit. I am the author
and accountable for all of it.

That is why `verify.py` exists. Every claim above is checked against the kernel's own serial
output and compared with hashes computed on the host, so you do not have to take the author's
word for anything — which matters more, not less, when the code was generated rather than typed.

## Repository layout

```
kernel/     stage1.asm entry.asm isr.asm kernel.c virtio.c store.c linker.ld
tools/      mkimage.py mkdisk.py verify.py history.py rawstore_check.py
M10-RAWSTORE.md          the on-disk format, commit protocol and pass condition
M14-BOOT.md              how the boot sector loads from a floppy, a stick or a disk
build.bat   run_vm.bat   test.bat   run_vm_debug.bat
HANDOVER.md BACKLOG.md   notes on state and what comes next
DRIVER-LIST.md           why there are no drivers, and how to compile your own list
```

`tools/history.py` takes numbered, hash-verified snapshots of the source before a build,
so a change that breaks the boot can be rewound in one command:

```
py tools\history.py --list
py tools\history.py --diff 3
py tools\history.py --restore 3    # prints its own undo command
```

## Notes for anyone else writing a kernel

Things that cost real time here, so they need not cost it twice:

- Legacy virtio: `QueueNum` is **read-only**; the device config lives at **BAR0 + 0x14**.
- An identity map of 1024 entries covers **4 MB each** — four entries is only 16 MB.
  Everything beyond that faults, which looks like a broken driver.
- Anything **the device writes** must be `volatile`, or `-O2` caches the value and the
  completion poll spins forever.
- Compile with `-mgeneral-regs-only`. Touching SSE before the IDT exists resets the CPU.
- A flat binary has no zero-filled segment, so `.bss` must be cleared by hand.

## License

MIT — see `LICENSE`.


## Project documents

| Document | What it covers |
|---|---|
| [CREDITS.md](CREDITS.md) | who contributed what, and how the work was produced |
| [CHANGELOG.md](CHANGELOG.md) | what changed, per version |
| [CONTRIBUTING.md](CONTRIBUTING.md) | how to send a change, and the house rules |
| [SECURITY.md](SECURITY.md) | known exposures, and how to report a problem |
