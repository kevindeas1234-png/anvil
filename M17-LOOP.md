# M17 — close the loop: something proposes a policy, the verifier judges it

**Status:** implemented, passing. `py tools\policy_loop.py`.
**Depends on:** M16 (policy is data; measurements exist).
**Answers:** charter directive 5 — that management improves as the intelligence directing it
improves.

## Why this is the honest next step

M16 built the seam, but a script still writes the policy by hand. The claim that better
judgement produces a better machine has never been *tested* — only made possible.

This milestone closes the loop with the smallest possible substitute for a future model:

```
measure baseline  ->  propose a policy  ->  run  ->  keep it only if output is identical and
                                                    cost is strictly lower
```

The proposer can be anything: a grid search today, a language model tomorrow, a person at a
terminal. **The kernel cannot tell the difference, and that is the point.** Swapping the
intelligence must not require touching the mechanism.

## The rule that keeps it honest

A proposal is accepted only if:

1. every check still passes, and
2. the committed region hash is **identical** to the baseline, and
3. at least one cost metric is strictly lower.

A policy that moves less data and changes one byte of output is **rejected**, not celebrated.
The loop must be able to say "faster and wrong", because that is the failure mode a system
being told to optimise will find first.

## What gets measured and kept

Every candidate run is recorded: policy, sectors moved, requests issued, region hash, verdict.
The result is a small, append-only record of *what was tried and what happened* — the same
evidence pattern as `bank.log` and the store's own log, applied to decisions instead of files.

    policy        chunk=4 flush=8   sectors=296  requests=104  region=0x50370dfe  ACCEPTED
    policy        chunk=8 flush=64  sectors=280  requests= 80  region=0x50370dfe  ACCEPTED
    policy        chunk=4 flush=0   sectors=352  requests=104  region=0x50370dfe  REJECTED (clamped)

That record is what makes a *later* claim about improvement checkable: not "the AI made it
faster", but this table.

## Pass condition

| # | Check | Expected |
|---|---|---|
| 1 | Baseline measured first | `defaults: 352 sectors, 352 requests, region 0x50370dfe` |
| 2 | At least one proposal accepted | a policy with fewer sectors or requests, identical hash |
| 3 | Every accepted proposal re-verified | full suite PASS on the accepted policy's own run |
| 4 | A deliberately bad proposal rejected | e.g. a policy that would alter chunk beyond the record size, reported as REJECTED |
| 5 | The loop is repeatable | two consecutive runs produce the same accepted policy and the same numbers |
| 6 | The record is append-only and outside the kernel | a plain file, one line per candidate |

Check 4 matters most. A loop that only ever accepts is a rubber stamp; it has to demonstrate
that it can refuse.

## What this deliberately is not

- **Not a model in the kernel.** Nothing here evaluates a prompt. The proposer is an external
  process; the kernel only ever reads 16 bytes.
- **Not an optimiser chasing one number.** Two costs are reported, and improvement in one at
  the expense of the other is a trade, not a victory — it gets recorded as one.
- **Not an autonomous agent.** It runs when asked, on a sandbox disk, and every step is a
  separate, inspectable log line.

## Order of work

1. `tools/policy_loop.py`: measure baseline, propose from a candidate list, run, judge, record.
2. Judge strictly: identical hash required; strictly lower cost required.
3. Include a candidate that must be rejected, so the loop proves it can refuse.
4. Write the decision record next to the logs, append-only.
5. Then M11 (compression policy) plugs into the same loop, with a third cost metric: bytes
   written.

## Result

Two consecutive runs, identical both times:

```
baseline         invalid (defaults)   sectors=352  requests=352  region=0x50370dfe  PASS
chunk4_flush8    chunk=4 flush=8      sectors=296  requests=104                    ACCEPTED
chunk1_flush16   chunk=1 flush=16     sectors=292  requests=292                    ACCEPTED
chunk2_flush32   chunk=2 flush=32     sectors=290  requests=162                    ACCEPTED
same_as_baseline chunk=1 flush=1      sectors=352  requests=352  REJECTED (not strictly cheaper)
corrupt_table    invalid (defaults)   sectors=352  requests=352  REJECTED (not strictly cheaper)
```

**3 accepted, 2 rejected, one region hash throughout.** The loop finishes by leaving the
winning policy on the disk, so the next run starts from the result rather than from scratch.

The rejections are the part worth keeping: a policy identical to the baseline is refused for
not being *strictly* cheaper, and a corrupted table falls back to the kernel's defaults and is
refused for the same reason. A loop that only ever accepts is a rubber stamp; this one
demonstrably says no.

The decision record is append-only, outside the kernel, one line per candidate:
`D:\AOS_Logs\policy_loop.log`.

## Two harness defects this exposed

**`run_vm.bat` could return before the guest had finished.** Its watchdog treated "QEMU not
seen yet" as "QEMU has exited" — so during start-up, before the process appeared, the batch
concluded the run was over, printed a partial log, and returned while QEMU was still writing.
Every automated test that read the log immediately afterwards was racing it. Fixed: the
watchdog now waits for the process to *appear* before believing it has gone, and pauses once
more after killing it.

**The loop read the log mid-flush.** Even with the watchdog fixed, the serial file can still
be settling when the batch returns, which produced a truncated log and a verification failure
that belonged to the tool, not the kernel. `wait_for_stable()` now waits until the size stops
changing.

Both were invisible while runs were done by hand and only surfaced once something automated
called them in a loop — which is an argument for automating, made by automating.

## What this proves, and what it does not

It proves that a proposal can be accepted only on identical output and strictly lower cost,
and that refusal works. It does not prove that a *model* would propose well: today's proposer
is a fixed candidate list. Swapping in a model changes one function and nothing else, which
was the design goal.
