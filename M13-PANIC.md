# M13 — a fault that explains itself

**Status:** implemented, passing.
**Depends on:** M3 (interrupt stubs) — the last unmet condition on the publishing bar in
`CHARTER.md`: *failure modes are logged, not silent.*

## The problem

Before this, a fault printed one line:

```
exception 0x00000006 eip=0x0001367e err=0x00000000 handled
```

That says a fault happened and where, but not what the kernel had been doing — which is
usually the only clue that matters. It also printed no name for the vector, so a reader has
to know that 13 means general protection and 14 means page fault.

## What it does now

```
exception 0x0000000d eip=0x00013688 err=0x00000028 handled
panic: vector=0x0000000d general protection
panic: eip=0x00013688 err=0x00000028
panic: recent output
  | tick 10
  | AOS TICK TEST COMPLETE
  |
  | ANVIL shell ready. type 'help'
  | anvil>
  | fault test: raising #UD
  | fault test: mode=general protection
  | exception 0x0000000d eip=0x00013688 err=0x00000028 handled
  | panic: vector=0x0000000d general protection
  | panic: eip=0x00013688 err=0x00000028
panic: halted
```

- **vector name** for the common faults (divide, invalid opcode, double fault, general
  protection, page fault), because `0x0d` is not self-explanatory
- **error code** — for `#GP` this is the offending selector, which is the actual diagnosis
- **the last twelve lines the kernel printed** — the context that usually identifies the bug
- **`panic: halted`** — and the VM exits through `isa-debug-exit` rather than resetting, so a
  watcher can tell "it panicked" from "it vanished"

The old `exception … handled` line is kept deliberately: existing checks depend on it, and
removing it to look tidy would have silently weakened the suite.

## Two faults, not one

The backlog asked for a null dereference. **That is impossible in this kernel:** the identity
map covers all 4 GiB, so address 0 is exactly as valid as any other. Rather than quietly
change the test, the kernel supports both:

| Selector | Fault raised | Vector |
|---|---|---|
| default | `ud2` — invalid opcode | `0x00000006` |
| policy reserved byte `0x0D` | load selector `0x28`, absent from the GDT | `0x0000000d` |

The selector lives in the policy table's reserved byte, so exercising a **real protection
fault** needs no rebuild — the same seam M16 established, reused.

## Two defects found while building it

**The panic report captured itself.** The ring buffer recorded every character written, and
the report was written through the same buffer — so it printed its own output, interleaved and
corrupted. Fixed with a `panic_quiet` flag: stop capturing, then report.

**The new check could not fail.** The panic-vector assertion was added inside a branch that
milestone-10 logs never reach, so `--expect-panic-vector 0x6` *passed* against a log whose
vector was `0x0d`. Caught by deliberately testing the negative. It is now one function called
from every branch that runs a fault test, and both wrong-vector directions fail as they must.

## Pass condition

| # | Check | Result |
|---|---|---|
| 1 | Default fault still reports as before | `exception 0x00000006 … handled` — PASS |
| 2 | Panic block present | vector, name, eip, err, recent output, halted |
| 3 | Exact vector asserted | `--expect-panic-vector 0x6` and `0xd` both PASS |
| 4 | Wrong vector rejected | both directions FAIL |
| 5 | Full suite unchanged | milestone 10, `RESULT: PASS` |
| 6 | Reaches the VM exit, does not reset | `panic: halted` then `isa-debug-exit` |

## What this closes

Condition 4 of the publishing bar — *failure modes are logged, not silent* — is now met. All
four conditions in `CHARTER.md` are satisfied for the kernel: every claim is script-checkable,
the limits are stated, there is a measured result contradicting the obvious approach, and a
fault now explains itself.
