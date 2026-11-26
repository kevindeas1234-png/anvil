# Changelog

All notable changes to ANVIL. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-26

### Added
- **M20: the kernel speaks to an agent, not to a reader.** Every state change is now also emitted as a
  record — `evt id=store.rec seq=8 index=0 hash=0x23f9753d chk=0xf7ef02f2` — with a monotonic
  sequence, a per-line FNV-1a checksum and a registered field set. Twenty-three records per boot,
  thirteen registered ids. `verify.py` recomputes every checksum on the host, rejects unknown ids,
  `seq` gaps and missing or undeclared fields, and compares four store hashes against the reference
  implementation. `tools/evt_inject.py` breaks four rules one at a time and all four are caught.

  The prose is untouched: still printed for humans, no longer what the harness asserts on. Kernel
  26,836 bytes of 32,768. Assertions 43 → 50; the share computed host-side 16% → 24%.

  Three bugs found in the milestone itself, all by a check disagreeing with an expectation rather
  than by reading the code: the checksum key `hash=` collided with a payload field of the same name
  (now `chk=`); the id pattern required a dot, so `tick` and `panic` failed to parse; and the call to
  `check_records` was placed after an earlier `return`, leaving `test.bat` green while the validation
  never ran. A fourth, in the injection harness, searched for an id that was not in the record it was
  editing, so one case passed while testing nothing.
- **The kernel has a screen.** Every character `print` / `put_dec` / `put_hex` sends to COM1
  is also mirrored to the 80x25 VGA text buffer at `0xB8000`, so the VM shows what it is doing
  instead of a blank window. `tools/capture_boot.py` drives QEMU's monitor to screendump the
  display and encodes the frames to PNG with nothing but zlib; `docs/boot.gif` is the result.
  The serial path is untouched, so `verify.py` sees byte-identical output and every hash is
  unchanged.

  Codegen note, because it cost an hour: `serial_putc` and `vga_putc` now carry
  `__attribute__((noinline))`. Once the console writer grew to include the VGA path, the
  compiler inlined ~600 bytes of it into hundreds of `kmain` and `shell` call sites, taking
  `.text` from 24 KB to 98 KB and overflowing the 32 KB boot window. `mkimage`'s size limit
  caught it. Out of line, the kernel is 24,476 bytes - smaller than before the change.
- **M11: pack or plain, decided by measurement.** The kernel run-length packs a
  1024-byte block when the policy says it is worth it (10 bytes, 512 written) and
  writes plain when it is not (1024 written), verifying both by FNV round trip.
  Refusing to pack noise is asserted, so the decision cannot quietly stop happening.
- **M17: the closed loop.** `tools/policy_loop.py` measures a baseline, proposes
  policies, and keeps one only if the output is byte-identical and the cost strictly
  lower. Three accepted (down to 290 sectors from 352), two refused, identical region
  hash throughout, decisions recorded append-only. The proposer is a candidate list
  today; swapping in a model changes one function.
- **M10: a raw record store with no partition table and no filesystem.** Fixed-size records
  at `LBA 16 + i*4`, a superblock at `LBA 0` holding the committed count, per-record FNV-1a,
  and a commit ordering that makes an uncommitted record harmless. `verify.py` compares four
  hashes against `tools/rawstore_check.py`, an independent implementation written first.
- **M14: geometry-independent boot.** `stage1.asm` probes for INT 13h extensions and reads
the kernel by LBA when they exist, falling back to the old CHS arithmetic when they do not.
The path taken is announced on COM1 as `L` or `C`, so a log states how it booted instead of
leaving it to be assumed. The same image now boots as a floppy (`C`) and as a USB mass
storage device (`L`), reaching identical store hashes. New: `run_vm_usb.bat`.
- **M16: the policy layer.** Chunk size and commit cadence come from a 16-byte table in the
device's last sector, so how much work the machine does is a decision made above the kernel
rather than compiled into it. Byte-identical output under every policy; `flush=16` moves 17%
fewer sectors, `chunk=4` issues 70% fewer requests. New: `tools/setpolicy.py`,
`tools/policy_test.py`.
- **M13: a fault that explains itself.** A fault now prints the vector by name, the error
  code, and the last twelve lines the kernel wrote, then halts and exits the VM instead of
  resetting silently. `#UD` is the default; a genuine `#GP` is available through the policy
  table's reserved byte, so the panic path is exercised with a real protection failure.
- `CREDITS.md` — contributors, and a precise statement of what the record does and does not
  prove.
- `DRIVER-LIST.md` — why one machine's driver inventory is a fingerprint, not a plan, and
  how to compile your own.
- `CONTRIBUTING.md`, `SECURITY.md`, `.gitattributes`.

### Changed
- Personal data removed from the documentation: IP addresses, MAC addresses, WiFi name,
  hostname, account paths and device names replaced with placeholders. Build specifics
  (versions, ports, file names) are kept, because they are what make this reproducible.
- Kernel load area doubled: `KERNEL_SECTORS` in `stage1.asm` and `KERNEL_LIMIT` in
  `mkimage.py` moved together from 32 to 64 sectors, because the store pushed the kernel
  past 16 KB. The two constants must always change as a pair.

### Fixed
- `run_vm.bat` could return before the guest finished writing: its watchdog treated
  "QEMU not seen yet" as "QEMU has exited", so a caller reading the log immediately
  got a truncated one. It now waits for the process to appear first.
- `store_format()` left the reserved sectors holding whatever was on the disk beforehand, so
  the region hash depended on the disk's history rather than on the formatter. Caught by the
  host disagreeing with the guest; fixed by zeroing the reserved area.
- `run_vm.bat` used `timeout /t`, which refuses to run when stdin is redirected. A
  scheduled or automated run therefore skipped the wait, killed the VM instantly and
  logged nothing — a confusing `FAIL` with no cause. Replaced with a console-independent
  watchdog that also exits early when the guest ends itself via `isa-debug-exit`.

## [0.1.0] — 2026-11-15

First tagged release.

### Added
- Boot path: boot sector → protected mode → kernel at `0x10000`.
- GDT, IDT, PIC remap, PIT, E820 memory map, identity-mapped paging, 1 MiB kernel heap.
- Interrupt stubs for every vector; a deliberate `#UD` is caught, reported with `eip` and
  `err`, and execution continues.
- virtio-blk read, write, read-back round trip, and an arbitrary-LBA read.
- `verify.py` checks the serial log and compares guest-computed FNV-1a hashes against
  values computed on the host. `test.bat` runs build, boot and verify in one command.