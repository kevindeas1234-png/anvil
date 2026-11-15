#!/usr/bin/env python3
"""Prove the policy layer: same bytes, less work.

Runs the store self-test twice with different policy tables and requires:

  1. both runs pass the full verifier,
  2. the committed region hash is **identical** under both,
  3. a larger `flush` moves strictly fewer sectors,
  4. a larger `chunk` issues strictly fewer requests.

Point 2 is the one that makes the claim meaningful. Without it, "better
management" is indistinguishable from "wrote less and hoped".

    py tools/policy_test.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGE = os.path.join(ROOT, "images", "testdisk.img")
SETPOLICY = os.path.join(HERE, "setpolicy.py")
VERIFY = os.path.join(ROOT, "verify.py")

CASES = (
    ("default", ["--clear"]),
    ("flush16", ["--chunk", "1", "--flush", "16"]),
    ("chunk4_flush8", ["--chunk", "4", "--flush", "8"]),
)


def run(cmd: list[str], cwd: str | None = None) -> int:
    return subprocess.call(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)


def measure(label: str, policy_args: list[str]) -> dict:
    run([sys.executable, SETPOLICY, "--image", IMAGE] + policy_args)
    label_name = f"policy_{label}"
    run(["cmd", "/c", "run_vm.bat", label_name], cwd=ROOT)
    log = os.path.join(ROOT, "logs", f"{label_name}.log")
    with open(log, encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    def grab(pattern: str, default: str = "?") -> str:
        match = re.search(pattern, text)
        return match.group(1) if match else default

    return {
        "label": label,
        "verify": run([sys.executable, VERIFY, log]),
        "policy": grab(r"store: policy ([^\r\n]*)"),
        "sectors": int(grab(r"store: moved (\d+) sectors", "0")),
        "requests": int(grab(r"store: moved \d+ sectors, (\d+) requests", "0")),
        "region": grab(r"store: region fnv=(0x[0-9a-fA-F]+)"),
    }


def main() -> int:
    if not os.path.isfile(IMAGE):
        print(f"[policy-test] no image at {IMAGE} - run build.bat first")
        return 2

    print("[policy-test] running three policies against the same suite\n")
    results = []
    for label, args in CASES:
        item = measure(label, args)
        results.append(item)
        print(f"  {label:<15} policy={item['policy']:<34} "
              f"sectors={item['sectors']:<5} requests={item['requests']:<5} "
              f"region={item['region']}  verify={'PASS' if item['verify'] == 0 else 'FAIL'}")
    print()

    ok = True

    def check(condition: bool, description: str) -> None:
        nonlocal ok
        print(f"  [{'PASS' if condition else 'FAIL'}] {description}")
        ok = ok and condition

    by = {item["label"]: item for item in results}

    for item in results:
        check(item["verify"] == 0, f"{item['label']} passes the full verifier")

    regions = {item["region"] for item in results}
    check(len(regions) == 1 and "?" not in regions,
          f"identical region hash under every policy ({', '.join(sorted(regions))})")

    check(by["flush16"]["sectors"] < by["default"]["sectors"],
          f"deferred commits move fewer sectors "
          f"({by['default']['sectors']} -> {by['flush16']['sectors']})")

    check(by["chunk4_flush8"]["requests"] < by["default"]["requests"],
          f"larger chunks issue fewer requests "
          f"({by['default']['requests']} -> {by['chunk4_flush8']['requests']})")

    print()
    print("RESULT:", "PASS - same bytes, demonstrably less work" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
