# M11 — pack or plain, decided by measurement

**Status:** implemented, passing.
**Depends on:** M16 (the policy table), M17 (the loop that judges proposals).
**Closes:** charter directive 4 in the kernel itself — efficiency as a decision, not a speed-up.

## The idea

`io_bench` showed it from outside the kernel: writing fewer bytes beats writing faster. M11 puts
that decision *inside*, where the kernel can measure the data before moving it.

A run-length coder is used deliberately: it is short enough to audit, and its worst case is
2x — which makes the refusal real rather than theoretical. Packing incompressible data costs
twice as much, exactly as `compress.py` measured from the user side.

## How the decision is made

```
pack the block  ->  if packed_size * 100 <= block_size * threshold:  write packed
                    else:                                            write plain
```

`threshold` comes from the policy table's `pack_threshold` field (byte 0x0C), default 90%,
clamped to 10..100. So the decision is data, not code — the same seam M16 established.

## Result

```
store: pack runs   1024 -> 10 bytes (packed), wrote 512, roundtrip ok
store: pack noise  1024 -> 1024 bytes (plain), wrote 1024, roundtrip ok
store: pack verdict runs packed, noise plain
```

| Data | Stored | Bytes written | Round trip |
|---|---|---|---|
| 1024 bytes of one repeated byte | 10 bytes, packed | **512** | verified by FNV |
| 1024 bytes of pseudo-random noise | 1024, plain | 1024 | verified by FNV |

Compressible data costs half the write. Noise is written as it is, because packing it would
have cost *more* — and the test fails if that refusal ever stops happening
(`noise was packed too - the threshold is too permissive`).

Both are read back and compared by hash, so "packed" cannot mean "corrupted and smaller".

## The bug the test found in itself

The first run failed on the noise case. The plain path wrote **two** sectors but read back
**one**, then compared 1024 bytes — so half the comparison was against stale buffer contents.
It now reads back every sector it wrote. A compression test that reads less than it wrote is
exactly the class of mistake this project keeps finding: the check, not the code, was wrong.

## What this does not prove

- **Not a general compressor.** RLE is chosen for auditability, not ratio. A real one
  (LZ4-class) would be a milestone of its own, and would need its own decoder in the kernel.
- **Not a filesystem.** This packs a block on its way to a raw LBA; there is no directory,
  no metadata, no random-access file layer.
- **Not faster.** Fewer bytes moved, which is the point; wall-clock is not claimed.

## Why it matters for the thesis

The kernel now makes a decision from a measurement it took, with a policy supplied from
outside, and a test that fails if the decision stops being made correctly. That is the shape
of "management improves as the intelligence directing it improves" — with a fixed candidate
list standing in for the intelligence until something better is plugged in.
