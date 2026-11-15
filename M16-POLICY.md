# M16 — let the policy be replaced, not the mechanisms

**Status:** specified, not implemented.
**Depends on:** M10 (the store works and is verified by hash).
**Answers:** charter directives 3, 4 and 5 — that the AI *directs* the machine, and that it
does so better as it improves.

## The gap this closes

The kernel already has mechanisms (read, write, verify) and measurements (bytes moved, device
capacity, hashes). What it does not have is a *replaceable policy*. Chunk size, flush cadence
and whether to pack data are compiled in. A better model arriving tomorrow would change
nothing at all — which is exactly the founding directive left unbuilt.

## The shape

```
measurement  ->  policy (replaced by whoever is directing)  ->  mechanism  ->  measurement
```

The kernel owns the two ends. The middle is data, not code.

### Policy table, stored on disk beside the superblock

Sixteen bytes in the reserved area (LBA 1), so a policy change needs no rebuild:

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0x00 | 4 | magic `ANVLPOL1` | identifies a valid table |
| 0x08 | 2 | `chunk_sectors` | how many sectors per write, 1..64 |
| 0x0A | 2 | `flush_every` | commit the superblock every N appends (1 = always) |
| 0x0C | 2 | `pack_threshold` | reserved for M11: pack when the sample compresses below this |
| 0x0E | 2 | `reserved` | zero |
| 0x10 | 4 | FNV-1a over 0x00..0x0F | integrity |
| 0x14 | 12 | zero | |

If the table is missing or fails its checksum, the kernel uses built-in defaults and says so
in the log. A corrupt policy must never stop a boot.

### Measurements the kernel must report

Counted in the mechanism, because only the mechanism can count them honestly:

- `store: policy chunk=8 flush=1` — what is in force
- `store: moved 512 sectors` — every write the store issued, including superblock rewrites
- `store: appended 64, count=64` — unchanged

Sectors moved is the currency. It is the one number that expresses "how much work did this
policy cause", and it is comparable across runs in a way that wall-clock time inside an
emulator is not.

## The invariant

**Policy may change cost. It may never change correctness.**

Whatever the table says, the records written must still verify, and the region hash must
still equal the host's independent computation. This is the same rule as the commit protocol:
the count is not negotiable, the chunking is.

If a policy makes the store write fewer sectors and the hashes still match, that is a genuine
improvement. If it makes the hashes differ, it is a bug regardless of how fast it was.

## Pass condition

Two policy tables, one unchanged test suite, one comparison. No partial credit.

| # | Check | Expected |
|---|---|---|
| 1 | Default policy boots and passes | milestone 10, `RESULT: PASS`, hash unchanged |
| 2 | Policy reported | `store: policy chunk=4 flush=1` present |
| 3 | Alternative policy, `chunk=16 flush=8` | also passes every existing check |
| 4 | Same data either way | `store: region fnv` identical under both policies |
| 5 | Cost differs, and is reported | `store: moved N sectors` lower for the larger chunk, with both numbers printed |
| 6 | Corrupt policy table | falls back to defaults, logs `store: policy invalid`, still passes |
| 7 | Missing policy table | falls back to defaults, logs `store: policy absent`, still passes |

Check 4 is the one that matters: **identical bytes, different cost.** Without it, "better
management" is indistinguishable from "wrote less and hoped".

Check 6 and 7 matter because a policy layer that can brick a boot is not an improvement. The
failure mode of bad policy must be *default behaviour*, never a hang.

## What this deliberately does not do

- **No self-modifying kernel.** The policy is data. It cannot add a mechanism, only choose
  among existing ones.
- **No model call from inside the kernel.** Nothing in ANVIL talks to a model. The policy
  table can be written by an agent, a person, or a script — the kernel neither knows nor
  cares, which is what keeps the mechanism testable.
- **No tuning for a synthetic benchmark.** The number that counts is sectors moved for the
  same verified output, not a score.

## Why this is the honest first step

"Let the AI manage the hardware" is unfalsifiable as stated. "Given two policy tables, one
moves fewer sectors and produces byte-identical output" is a claim with a pass condition,
which can be wrong. The second one is worth building.

## Order of work

1. Policy table read, validate, and default-on-failure, with the two log lines.
2. Chunk size honoured by the write path; sector counter added to the mechanism.
3. `test.bat` runs both policies and prints both sector counts.
4. `verify.py` asserts identical region hashes and a strictly lower count for the larger
   chunk.
5. Then M11 — compression policy — reuses the same table and the same invariant.
