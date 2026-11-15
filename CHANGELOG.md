# Changelog

All notable changes to ANVIL. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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