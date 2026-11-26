#!/usr/bin/env python
"""Prove the record checks actually fail when they should.

A verifier that has never failed is a verifier nobody has tested. Each case
below breaks exactly one rule and expects verify.py to exit non-zero:

  checksum  - flip a hex digit in chk=              (does the hash catch it?)
  sequence  - delete one record line                (is a gap noticed?)
  unknown   - rename an id and fix the checksum     (is the registry enforced?)
  field     - drop a field and fix the checksum     (are required fields checked?)

Cases 3 and 4 repair the checksum on purpose. Without that they would fail for
the checksum, and the rule they are meant to test would never be exercised -
which is the mistake this whole project keeps finding in other people's tests.

    py tools\\evt_inject.py [log]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from verify import fnv1a32          # noqa: E402  (path set above)


def rebuild(head: str) -> str:
    """Re-checksum a record whose text before ' chk=' was rewritten."""
    return f"{head} chk=0x{fnv1a32(head.encode('ascii')):08x}"


def records(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if line.startswith("evt ")]


def case_checksum(lines: list[str]) -> list[str]:
    out = list(lines)
    index = records(out)[0]
    line = out[index]
    head, chk = line.rsplit(" chk=", 1)
    digit = "0" if chk[3] != "0" else "1"          # flip one hex digit of the checksum
    out[index] = f"{head} chk={chk[:3]}{digit}{chk[4:]}"
    return out


def case_sequence(lines: list[str]) -> list[str]:
    out = list(lines)
    del out[records(out)[1]]                        # remove the second record
    return out


def case_unknown_id(lines: list[str]) -> list[str]:
    out = list(lines)
    index = records(out)[2]
    head = out[index].rsplit(" chk=", 1)[0]
    rewritten = re.sub(r"id=[a-z0-9.]+", "id=tock", head, count=1)
    # The first version of this searched for 'id=tick' in a record whose id was
    # 'page.on': the substitution changed nothing, the checksum still matched,
    # and the case passed while testing absolutely nothing. Now a no-op is loud.
    if rewritten == head:
        raise SystemExit(f"injection did not change record {index}: {head!r}")
    out[index] = rebuild(rewritten)
    return out


def case_missing_field(lines: list[str]) -> list[str]:
    out = list(lines)
    for index in records(out):
        head = out[index].rsplit(" chk=", 1)[0]
        if "id=store.pack" in head:
            stripped = re.sub(r" bytes_out=[0-9]+", "", head, count=1)
            out[index] = rebuild(stripped)
            return out
    raise SystemExit("no store.pack record in the log to strip a field from")


CASES = (
    ("checksum", case_checksum, "checksum does not match"),
    ("sequence", case_sequence, "sequence gap"),
    ("unknown", case_unknown_id, "unknown id"),
    ("field", case_missing_field, "missing"),
)


def main(argv: list[str]) -> int:
    log = argv[0] if argv else os.path.join(ROOT, "logs", "m1test.log")
    with open(log, encoding="utf-8", errors="replace") as handle:
        original = handle.read().splitlines()

    print(f"[inject] base log: {log}")
    print(f"[inject] {len(records(original))} records\n")

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, mutate, expected in CASES:
            path = os.path.join(tmp, f"{name}.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(mutate(original)) + "\n")

            run = subprocess.run([sys.executable, os.path.join(ROOT, "verify.py"), path],
                                 capture_output=True, text=True)
            caught = run.returncode != 0
            specific = expected in run.stdout

            if caught and specific:
                print(f"  [PASS] {name:9s} rejected ({expected!r} reported)")
            elif caught:
                print(f"  [WARN] {name:9s} rejected, but not for {expected!r} "
                      f"- the case is testing the wrong rule")
                failures += 1
            else:
                print(f"  [FAIL] {name:9s} was ACCEPTED - the check is blind to it")
                failures += 1

    print(f"\nRESULT: {'PASS' if failures == 0 else 'FAIL'} "
          f"({len(CASES) - failures}/{len(CASES)} injections caught)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
