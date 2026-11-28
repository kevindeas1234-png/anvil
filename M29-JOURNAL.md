# M29 — the machine keeps a journal

**Status:** specification. Blocked on M24 (the machine must be able to write to its own medium).
**Depends on:** M24/M25 (BIOS disk write), M10 (record store), M20 (event records).
**Sandbox:** unchanged.

## The question this answers

An agent's context ends when the conversation does. That is not decay — it is deletion, and it is
the reason a session cannot be held accountable for what an earlier one decided.

Can the *machine* hold the memory instead? Not in the model's context, and not in a human's notes,
but in its own store, in a form that a later agent can read and **verify**: append-only, hash-chained,
and loud when something is missing.

## Why this must wait for M24

The only block device today is virtio-blk, which exists only inside QEMU. A journal written through
it would be memory that dies with the emulator — the exact failure this milestone exists to escape.
**Persistence on metal is the prerequisite, not a detail.**

## Design

Two structures, deliberately different:

### The journal — append-only

One record per event, fixed size, in the store's existing 4-sector layout:

| Field | Meaning |
|---|---|
| `seq` | monotonic, no gaps |
| `kind` | `boot`, `decision`, `outcome`, `symptom`, `checkpoint` |
| `prev` | FNV-1a of the previous record's payload (zero for the first) |
| `hash` | FNV-1a of this record's payload |
| `payload` | text, bounded by the record size |

The chain is the point. A truncated journal has a `seq` gap; an edited record has a `prev`/`hash`
mismatch; a deleted tail is visible because the committed count in the superblock does not match the
last record's `seq`. **Absence is an error, never a blank** — the same rule as `verify.py`.

### The resume note — bounded and mutable

The journal answers "what happened". It does not answer "what now". A single fixed-size sector at a
known LBA holds:

```
last seq seen | last state (one line) | next step (one line) | open question (one line)
```

Rewritten at each checkpoint. Bounded on purpose: an unbounded "memory" grows until nobody reads it,
which is how every previous scheme of this kind has died.

## Pass condition

1. Append N journal entries, reboot, and the chain verifies — `seq` continuous, every `prev` matching.
2. **Tamper one byte** in a record → detected and named, not silently accepted.
3. **Truncate the tail** → detected, because the committed count disagrees with the last `seq`.
4. The resume note survives a reboot and reads back exactly as written.
5. A later session, given only the medium and no conversation, can answer: what was the last state,
   what is the next step, and what failed last.

## Order of work

1. Record layout and the chain arithmetic, with a host-side reference (`tools/journal_check.py`)
   written **first** — the M10 pattern, because a checker written afterwards agrees with whatever
   was built.
2. Append and verify in the kernel, over whichever block path exists (virtio in QEMU, BIOS on metal).
3. Resume note read/write.
4. Emit a `journal` record at boot: chain length, head hash, and whether verification passed.
5. Tamper test, truncation test, both as injections.

## What this deliberately is not

- **Not a filesystem.** No names, no directories, no variable-size files.
- **Not a database.** No queries, no indexes beyond `seq`.
- **Not a transcript.** It records states and decisions, not conversation.
- **Not encryption.** Integrity is the goal here; confidentiality is a separate milestone with its own
  threat model, and it must not be assumed from a hash chain.

## What this proves, and does not

**Proves:** the machine can hold a verifiable record of its own history, and a fresh session can
resume from it without a human retelling anything.

**Does not prove:** that the record is *true*. A well-formed journal can contain a lie, exactly as a
well-formed event record can. Truth still rests on the rule that has held all along — values the host
computes independently, and a human accountable for the whole.
