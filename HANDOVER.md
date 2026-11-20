# Handover — ANVIL / io_bench

Written 2026-10-27. Everything below is on disk; nothing depends on this chat.

## State

| Component | Where | Status |
|---|---|---|
| ANVIL kernel | `D:\OS` | milestones 0–8 green; M9 (arbitrary-LBA read) green 2026-11-15 |
| io_bench | `D:\io_bench` | complete, MIT, findings in README |
| AOS pipeline | `D:\AOS` | working, runs 1–2 archived |
| Skills | global | `anvil-kernel`, `io-bench`, `aos-audit` |
| Backlog | Linear (ANVIL / io_bench) + `D:\OS\BACKLOG.md` | 8 open issues |

## The test

`D:\OS\test.bat` — build, boot headless, verify. All local, no quota. Last run
2026-11-15: `RESULT: PASS`, milestone 9 detected.

Pass looks like:

```
virtio: capacity=16384 sectors (hi=0)
virtio: used idx=1 status=0
virtio: sector0 fnv=0x206ce5e2
virtio: sector1 written
virtio: writeback fnv=0xe161b9c5 roundtrip ok
[PASS] sector0 0x206ce5e2 (host expects 0x206ce5e2)
[PASS] writeback 0xe161b9c5 (host expects 0xe161b9c5)
RESULT: PASS
```

The log lands in `D:\OS\logs\`. `run_vm.bat` uses `-snapshot`, so guest writes are
discarded: the image hashes are identical before and after a run in which the guest
writes a sector. Read the log before asking anything about the kernel's state.

If it fails: `D:\OS\run_vm_debug.bat` adds QEMU's exception trace to `logs\debug_qemu.log`.

## Rollback

`test.bat` snapshots the source tree before every build.

```
py D:\OS\tools\history.py --list
py D:\OS\tools\history.py --diff N
py D:\OS\tools\history.py --restore N     # prints its own undo
```

Note: run 0001 is the *current* M8 tree, not a known-good baseline. The first
snapshot taken after a green test is the one worth restoring to.

## Next actions, in order

1. Run `test.bat`; if green, close KEV-8 with the two hashes as evidence.
2. M9 — read an arbitrary sector (`blk_read(lba, buf)`), proof: sector 2048 hash.
3. M10 — panic handler (vector, error, EIP) instead of a triple fault.
4. io_bench: 1 GiB sustained run on both drives (burst figures are flattering).
5. Publish io_bench: `git init`, remote, write-up ("write less, not faster").

## Facts worth not rediscovering

- Legacy virtio: QueueNum is **read-only**; device config is at **BAR0+0x14**.
- The identity map is **1024 entries of 4 MB** — four entries only cover 16 MB.
- Anything the **device writes** must be `volatile`, or `-O2` caches it and the poll hangs.
- Compile C with `-mgeneral-regs-only`; SSE before the IDT exists resets the machine.
- `.bss` must be zeroed by hand; a flat binary has no zero-filled segment.
