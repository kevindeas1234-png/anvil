#!/usr/bin/env python3
"""M17 - close the loop: something proposes a policy, the verifier judges it.

M16 made it possible for better judgement to improve the machine. This tests it.

    measure the baseline  ->  propose a policy  ->  run  ->  keep it only if the
    output is byte-identical AND the cost is strictly lower

The proposer here is a fixed list; tomorrow it can be a model or a person. The kernel
cannot tell the difference, and that is the point.

A proposal is ACCEPTED only when:
  1. the full verifier still passes, and
  2. the committed region hash equals the baseline's, and
  3. sectors moved (then requests) are strictly lower than the best so far.

Anything else is REJECTED, with the reason recorded. A loop that only ever accepts is a
rubber stamp, so the candidate list deliberately includes policies that must fail: one that
costs the same, and one with a corrupted table that falls back to defaults.

    py tools\\policy_loop.py
    py tools\\policy_loop.py --dry-run     # show the candidates, change nothing
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGE = os.path.join(ROOT, "images", "testdisk.img")
SETPOLICY = os.path.join(HERE, "setpolicy.py")
VERIFY = os.path.join(ROOT, "verify.py")
RECORD = os.environ.get("AOS_POLICY_LOG", r"D:\AOS_Logs\policy_loop.log")

# (label, setpolicy args). The last two are expected to be refused - that is the test.
CANDIDATES = (
    ("chunk4_flush8", ["--chunk", "4", "--flush", "8"]),
    ("chunk1_flush16", ["--chunk", "1", "--flush", "16"]),
    ("chunk2_flush32", ["--chunk", "2", "--flush", "32"]),
    ("same_as_baseline", ["--chunk", "1", "--flush", "1"]),
    ("corrupt_table", ["--corrupt"]),
)


def wait_for_stable(log: str, timeout: float = 6.0) -> None:
    """QEMU's serial file can still be flushing when the batch returns.

    Reading it too early gives a valid-looking but truncated log, and the verifier
    then reports a failure that belongs to this tool rather than to the kernel.
    """
    last = -1
    deadline = time.time() + timeout
    while time.time() < deadline:
        size = os.path.getsize(log) if os.path.isfile(log) else 0
        if size > 0 and size == last:
            return
        last = size
        time.sleep(0.25)


def run(cmd: list[str], cwd: str | None = None) -> int:
    return subprocess.call(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)


def record(line: str) -> None:
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with io.open(RECORD, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {line}\n")


def measure(label: str, policy_args: list[str]) -> dict:
    """Set a policy, boot, verify, and read the numbers out of the log."""
    run([sys.executable, SETPOLICY, "--image", IMAGE] + policy_args)
    run_name = f"loop_{label}"
    run(["cmd", "/c", "run_vm.bat", run_name], cwd=ROOT)
    log = os.path.join(ROOT, "logs", f"{run_name}.log")
    wait_for_stable(log)
    text = io.open(log, encoding="utf-8", errors="replace").read()

    def grab(pattern: str) -> str:
        m = re.search(pattern, text)
        return m.group(1) if m else ""

    return {
        "label": label,
        "verified": run([sys.executable, VERIFY, log]) == 0,
        "policy": grab(r"store: policy ([^\r\n]*)"),
        "sectors": int(grab(r"store: moved (\d+) sectors") or 0),
        "requests": int(grab(r"store: moved \d+ sectors, (\d+) requests") or 0),
        "region": grab(r"store: region fnv=(0x[0-9a-fA-F]+)"),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="propose policies, let the verifier judge")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not os.path.isfile(IMAGE):
        print(f"[loop] no image at {IMAGE} - run build.bat first")
        return 2

    if args.dry_run:
        print("[loop] candidates:")
        for label, options in CANDIDATES:
            print(f"  {label:<18} {' '.join(options)}")
        return 0

    print("[loop] measuring the baseline (no policy table, kernel defaults)\n")
    baseline = measure("baseline", ["--clear"])
    if not baseline["verified"]:
        print("[loop] the baseline itself does not verify - stopping")
        return 1
    print(f"  baseline  {baseline['policy']:<24} sectors={baseline['sectors']:<5} "
          f"requests={baseline['requests']:<5} region={baseline['region']}  PASS")
    record(f"baseline {baseline['policy']} sectors={baseline['sectors']} "
           f"requests={baseline['requests']} region={baseline['region']} ACCEPTED (reference)")

    best = baseline
    accepted = 0
    rejected = 0

    for label, options in CANDIDATES:
        print(f"\n[loop] proposing {label}")
        result = measure(label, options)
        verdict, reason = judge(result, best, baseline)
        print(f"  {label:<18} {result['policy']:<24} sectors={result['sectors']:<5} "
              f"requests={result['requests']:<5} region={result['region'] or '-'}  "
              f"{verdict}" + (f" ({reason})" if reason else ""))
        record(f"proposal {label} policy={result['policy'] or '-'} "
               f"sectors={result['sectors']} requests={result['requests']} "
               f"region={result['region'] or '-'} {verdict}"
               + (f" ({reason})" if reason else ""))
        if verdict == "ACCEPTED":
            accepted += 1
            best = result
        else:
            rejected += 1

    # leave the machine on the winning policy, so a later run starts from the result
    options = dict(CANDIDATES).get(best["label"], ["--clear"])
    run([sys.executable, SETPOLICY, "--image", IMAGE] + options)

    print("\n[loop] summary")
    print(f"  best      {best['label']}  sectors={best['sectors']} requests={best['requests']}")
    print(f"  accepted  {accepted}")
    print(f"  rejected  {rejected}")
    print(f"  record    {RECORD}")
    ok = accepted >= 1 and rejected >= 1 and best["region"] == baseline["region"]
    print("\nRESULT:", "PASS - a proposal improved the machine with byte-identical output"
          if ok else "FAIL")
    return 0 if ok else 1


def judge(result: dict, best: dict, baseline: dict) -> tuple[str, str]:
    """The whole point: identical output, strictly lower cost, or nothing."""
    if not result["verified"]:
        return "REJECTED", "does not pass the verifier"
    if result["region"] != baseline["region"]:
        return "REJECTED", "output differs from the baseline - faster and wrong"
    if result["sectors"] == 0:
        return "REJECTED", "no measurements in the log"
    if (result["sectors"], result["requests"]) < (best["sectors"], best["requests"]):
        return "ACCEPTED", ""
    return "REJECTED", "not strictly cheaper than the best so far"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
