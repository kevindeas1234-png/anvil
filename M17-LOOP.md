# M17 — close the loop: something proposes a policy, the verifier judges it

**Status:** specified, not implemented.
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
