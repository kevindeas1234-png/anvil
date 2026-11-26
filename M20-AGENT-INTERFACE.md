# M20 — the kernel speaks to an agent, not to a reader

**Status:** specification. Nothing implemented yet.
**Depends on:** M10 (record store), M13 (panic), the existing serial path.
**Sandbox:** unchanged. `-snapshot`, file-backed images, no NIC, no real device.

## The question this answers

An AOS built for AI has an agent as its client. Can that agent read the kernel's state without a
human translating prose for it?

Today it cannot. The kernel emits English — `store: pack runs   1024 -> 10 bytes (packed)` — and
the only machine consumer, `verify.py`, recovers the numbers with a regular expression. That is a
human interface with a machine translator bolted on. Every assertion in the suite is one wording
change away from silently not testing anything, and a rewording would pass review because the prose
would still read correctly.

The question for this milestone is narrow: **what does the kernel have to emit so that verification
stops depending on English?**

## Why this comes before capabilities, audit and the manifest

Because all three need a channel that already exists and is already trusted. A capability table with
no way to report its decisions is an assertion. An audit log with no record format is a file full of
sentences. This milestone builds the channel; the others fill it.

It is also the cheapest step that changes the project's character rather than its size: no new
device, no new mode, no new memory.

## Design principle: the record is the contract, the prose is decoration

One line of output can be two things: something a human reads, and something a program asserts on.
They have different failure modes. Prose fails by being reworded; a record fails by being absent or
malformed. So the record is what the harness asserts on, and the prose is kept only because humans
watch the VM and read panic dumps.

If they ever disagree, the record wins and the prose is the bug.

## The record grammar

One record per line, prefixed `evt `, fields as `key=value`, single spaces, printable ASCII, no
quotes, no escaping.

```
evt id=store.pack seq=41 bytes_in=1024 bytes_out=10 writes=512 verdict=packed hash=0x1a2b3c4d
```

| Field | Type | Rule |
|---|---|---|
| `id` | dotted name | lowercase `[a-z0-9]+(\.[a-z0-9]+)+`, from the registered list |
| `seq` | u32 decimal | strictly increasing by 1, starts at 1, no gaps |
| `hash` | `0x` + 8 hex | FNV-1a 32 over the **bytes of the line up to, but not including, ` hash=`** |
| others | per `id` | `[A-Za-z0-9._:-]{1,64}`, no spaces, no `=` |

Everything after ` hash=` is the checksum and nothing else, so a truncated line cannot still verify.

The hash covers the literal emitted bytes rather than a re-serialised form on purpose: it needs no
agreement about field order beyond "as emitted", and a parser can check it without trusting its own
formatting.

### Registered events for this milestone

| `id` | Fields | Replaces the prose assertion |
|---|---|---|
| `boot.mem` | `kb=` | `memory: … KB conventional` |
| `boot.e820` | `entries=`, `usable_kb=` | `e820: 7 entries, 255 MB usable` |
| `page.on` | `verified=1` | `paging: remap verified` |
| `heap.ok` | `arena=`, `size_kb=` | `heap: alloc/free ok` |
| `virtio.read` | `lba=`, `hash=` | `virtio: sector0 fnv=…` |
| `virtio.write` | `lba=`, `hash=` | `virtio: writeback fnv=…` |
| `store.pack` | `bytes_in=`, `bytes_out=`, `writes=`, `verdict=` | `store: pack verdict …` |
| `store.rec` | `index=`, `hash=` | `store: rec0 fnv=…` |
| `store.region` | `hash=` | `store: region fnv=…` |
| `store.super` | `hash=` | `store: superblock fnv=…` |
| `store.replay` | `identical=1` | `store: replay identical` |
| `panic` | `vector=`, `eip=`, `err=` | `panic: vector=…` |
| `tick` | `n=` | `tick 1` … `tick 10` |

`store.rec` and `panic` are the important ones: their `hash`/`eip` values are compared against values
computed on the host, so the guest is reporting a measurement, not its own opinion of one.

## Why not JSON

JSON needs escaping, which means a string writer, which means allocation or a hand-rolled escaper in a
kernel with 8 KiB free. `key=value` needs neither, is greppable by a human reading the log, and parses
in about forty lines of Python that a critic can read in full. The cost is that values cannot contain
spaces — which nothing in the registry needs.

If a field ever needs prose inside it, that field is the wrong field.

## The strictness rules

These are what make the records worth more than the prose:

1. **Unknown `id` is a failure**, not something to ignore. A silently dropped emitter is otherwise invisible.
2. **A `seq` gap is a failure.** Records cannot be lost quietly.
3. **A `hash` mismatch is a failure**, including a line that fails to parse at all.
4. **A missing field for a known `id` is a failure.** Fields are mandatory unless the doc says optional.
5. **A field present but unparseable as its type is a failure** — `seq=4x1` must fail, not coerce.

A suite that only checks the records it recognises would pass on an empty log. Rule 1 exists for that.

## Size budget and the codegen trap

Estimated cost: 60–120 bytes of code per event id, plus one emitter, plus `put_hex` reuse. Call it
**+2 KiB**, against 8,292 free.

The emitter must be **one out-of-line function** (`__attribute__((noinline))`). This is not style:
inlining a 600-byte console writer into hundreds of call sites took `.text` from 24 KB to 98 KB and
overflowed the boot window yesterday. An emitter called from thirteen sites is exactly the shape that
repeats that mistake.

If the budget is exceeded, the answer is fewer registered events in this milestone, not a bigger
`KERNEL_SECTORS`.

## The panic path

`panic` must emit a record before halting. Two constraints from M13 apply unchanged:

- It must not capture itself into the recent-output ring — `panic_quiet` is set first.
- It must not allocate, and must not depend on the emitter's own state being sane.

If the emitter cannot produce a valid record during a panic, it emits nothing rather than a
malformed one. A malformed record is worse: it fails the hash check and hides the real fault.

## Pass condition

1. `test.bat` returns PASS, with every store and virtio assertion in `verify.py` asserting on
   **record fields** rather than prose.
2. `verify.py` fails if a `seq` is missing, an `id` is unknown, a `hash` is wrong, or a registered
   field is absent — each proven by an injection test, not by inspection.
3. A deliberately corrupted record (one byte flipped in `bytes_out`) makes the suite FAIL.
4. Kernel still under 32,768 bytes; free space reported in the build output.

## Order of work

1. Emitter, `seq` counter, `hash` computation. One record: `evt id=boot.mem seq=1 kb=639 hash=…`.
2. Convert `store.*` events. Prose stays.
3. `verify.py`: strict parser, registry of known ids, field assertions. Keep the prose assertions
   until step 4 proves they are redundant, then delete them in one commit that says so.
4. Convert `virtio.*`, `panic`, `tick`.
5. Injection tests: corrupt one byte, drop one record, add one unknown id. All three must FAIL.
6. Changelog entry; this document's status line changes to "implemented, passing".

## What this deliberately is not

- **Not JSON.** See above; the reason is escaping, not taste.
- **Not a schema validator in the kernel.** The kernel emits; the host asserts. A kernel that validates
  its own records can still be wrong about what it emitted.
- **Not capabilities (M21).** Nothing is enforced here. A record reports; it does not permit.
- **Not an RPC layer.** The agent still talks to the serial port. Framing requests for the agent to
  send is a later milestone, and it is worth having the outbound channel right first.
- **Not the removal of the human shell.** The prose stays where a human needs it, especially in panic
  dumps.

## What this proves, and what it does not

**Proves:** the kernel's interface is machine-first. Verification no longer depends on English
wording, so a reworded message can no longer silently delete a test. The agent can read state as
fields and assert on it.

**Does not prove:** that the records are honest. A kernel can emit a well-formed lie, and the hash
only proves the line was not corrupted in transit — not that the number inside is true. Honesty here
rests on the same thing it rested on before: values that the host computes independently and compares.
That is M10's design, and this milestone preserves it rather than replacing it.

**Also does not:** enforce anything about the agent's behaviour. Capability checks are M21, and until
there is a privilege boundary they are advisory — the agent's own code could bypass them. That is
stated plainly here so nobody discovers it later and calls it a flaw.
