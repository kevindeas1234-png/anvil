#!/usr/bin/env python
"""Run the gate twice and compare the results. Nondeterminism is a defect.

A suite that passes on Tuesday and fails on Tuesday is worse than no suite: it
teaches everyone to re-run until green. This tool exists because one such bug
(the watchdog killing the guest before its panic block) was found by a human
noticing that two consecutive runs disagreed - the least reliable detector there
is, and the only one available at the time.

  py tools\\flake_check.py          # two boots
  py tools\\flake_check.py 3        # three boots

Exit codes: 0 identical, 1 the runs disagreed, 2 a run could not be read.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def boot(name: str) -> str:
    """Boot the VM once under a fresh log name; return the log path."""
    log = os.path.join(ROOT, "logs", name + ".log")
    if os.path.exists(log):
        os.remove(log)
    subprocess.run(["cmd", "/c", os.path.join(ROOT, "run_vm.bat"), name],
                   cwd=ROOT, capture_output=True)
    if not os.path.exists(log):
        raise SystemExit(f"no log produced for {name}")
    return log


def signature(log_path: str) -> dict:
    """Everything a reader would use to decide 'pass', reduced to comparable form."""
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    # Records: the checksum is content-derived, so keeping it also catches a
    # record whose value changed between boots.
    records = set()
    for line in text.splitlines():
        if line.startswith("evt "):
            records.add(line)

    run = subprocess.run([sys.executable, os.path.join(ROOT, "verify.py"), log_path],
                         capture_output=True, text=True)
    fails = sorted(set(re.findall(r"\[FAIL\] (.+)$", run.stdout, re.M)))
    passes = re.findall(r"\[PASS\]", run.stdout)

    prose = [line for line in text.splitlines() if line and not line.startswith("evt ")]

    return {
        "exit": run.returncode,
        "records": records,
        "fails": fails,
        "pass_count": len(passes),
        "prose": prose,
        "final": text.strip().splitlines()[-1] if text.strip() else "",
    }


def describe(diff_kind: str, items) -> str:
    shown = list(items)[:4]
    more = f" (+{len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    body = "; ".join(str(item)[:80] for item in shown)
    return f"  {diff_kind}: {body}{more}"


def main(argv: list[str]) -> int:
    runs = int(argv[0]) if argv else 2
    if runs < 2:
        raise SystemExit("need at least two runs to compare")

    signatures = []
    for index in range(1, runs + 1):
        name = f"flake{index}"
        log = boot(name)
        sig = signature(log)
        signatures.append(sig)
        print(f"  run {index}: exit={sig['exit']}  records={len(sig['records'])}  "
              f"pass={sig['pass_count']}  fail={len(sig['fails'])}  "
              f"last={sig['final'][:40]!r}")

    base = signatures[0]
    problems = 0
    for index, sig in enumerate(signatures[1:], 2):
        if sig["exit"] != base["exit"]:
            print(f"  [FAIL] run {index} exited {sig['exit']}, run 1 exited {base['exit']}")
            problems += 1
        if sig["records"] != base["records"]:
            missing = base["records"] - sig["records"]
            added = sig["records"] - base["records"]
            print(f"  [FAIL] records differ between run 1 and run {index}")
            if missing:
                print(describe("only in run 1", missing))
            if added:
                print(describe("only in run " + str(index), added))
            problems += 1
        if sig["fails"] != base["fails"]:
            print(f"  [FAIL] failing assertions differ between run 1 and run {index}")
            print(describe("run 1", base["fails"]))
            print(describe("run " + str(index), sig["fails"]))
            problems += 1
        if sig["prose"] != base["prose"]:
            # Prose may legitimately differ (timestamps, addresses); say so rather
            # than failing, but never silently.
            first = next((a for a, b in zip(base["prose"], sig["prose"]) if a != b), "<length>")
            print(f"  [SKIP] prose differs (not a failure, but not ignored): first at {first[:60]!r}")
            problems += 1 if sig["exit"] != 0 else 0

    print()
    if problems == 0:
        print(f"RESULT: PASS ({runs} runs identical)")
        return 0
    print(f"RESULT: FAIL ({problems} difference(s) across {runs} runs)")
    print("  A suite that disagrees with itself cannot be trusted to judge anything else.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
