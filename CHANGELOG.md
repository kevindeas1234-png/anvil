# Changelog

All notable changes to ANVIL. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `CREDITS.md` — contributors, and a precise statement of what the record does and does not
  prove.
- `DRIVER-LIST.md` — why one machine's driver inventory is a fingerprint, not a plan, and
  how to compile your own.
- `CONTRIBUTING.md`, `SECURITY.md`, `.gitattributes`.

### Changed
- Personal data removed from the documentation: IP addresses, MAC addresses, WiFi name,
  hostname, account paths and device names replaced with placeholders. Build specifics
  (versions, ports, file names) are kept, because they are what make this reproducible.

### Fixed
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