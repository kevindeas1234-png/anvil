"""Milestone checker for AOS boot logs.

    M0  boot sector only     : banner, memory size, AOS HALT
    M1  protected mode       : + 32-bit mode, ticks 1..10 from the PIT handler
    M2  memory map + paging  : + E820 summary, MMU on, remap verified
    M3  exception handling   : + a deliberate #UD caught and reported
    M4  C kernel             : + freestanding C running at 0x10000

Exit codes: 0 = pass, 1 = fail, 2 = no log found (usable as a CI gate).

    python verify.py                 # newest log in logs\
    python verify.py logs\boot.log   # specific log
"""

from __future__ import annotations

import glob
import os
import re
import sys

M0_MARKERS = (
    "AOS KERNEL ONLINE",
    "memory: ",
    " KB conventional",
)

M1_MARKERS = (
    "protected mode: 32-bit",
    "AOS TICK TEST COMPLETE",
)

M2_MARKERS = (
    "e820: ",
    "paging: enabled",
    "paging: remap verified",
)

M3_MARKERS = (
    "fault test: raising #UD",
    "exception 0x00000006",
    " handled",
)

M4_MARKERS = (
    "ANVIL KERNEL ONLINE",
    "anvil: started",
    "anvil: serial online",
)

M7_MARKERS = (
    "virtio: found at",
    "virtio: sector0 fnv=",
)

M8_MARKERS = (
    "virtio: sector1 written",
    "virtio: writeback fnv=",
    "roundtrip ok",
)

M9_MARKERS = (
    "virtio: sector2048 fnv=",
)

M10_MARKERS = (
    "store: formatted ",
    "store: appended 64, count=64",
    "store: verify ok 64/64",
    "store: orphan ignored",
    "store: verify FAIL rec 32",
    "store: out of range 64",
    "store: full 4092",
    "store: replay identical",
)

# The expected hashes come from the reference implementation, not from a second
# copy of the kernel's assumptions. If the two disagree, that is the finding.
try:
    import rawstore_check as storeref
except ImportError:
    # it lives in tools\; add that to the path rather than duplicating the
    # format logic here
    _tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
    if _tools not in sys.path:
        sys.path.insert(0, _tools)
    try:
        import rawstore_check as storeref
    except ImportError:
        storeref = None

LBA_MARKER = 2048

TICK_LINE = re.compile(r"^\s*tick\s+(\d+)\s*$", re.MULTILINE)


def fnv1a32(data: bytes) -> int:
    """Same hash the kernel computes, so the two can be compared."""
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value

# Ordered progress markers. The last one present tells us where a crash
# happened when the log stops early.
STAGE_MARKERS = (
    # milestone 1-3 (assembly kernel)
    "stage2: entered",
    "gdt: loaded",
    "pmode: entered",
    # milestone 4+ (C kernel), in the order kmain prints them
    "anvil: started",
    "anvil: serial online",
    "memory: ",
    "protected mode: 32-bit",
    "idt: ready",
    "pic: remapped",
    "pit: started",
    "e820: ",
    "paging: enabled",
    "paging: remap verified",
    "heap: arena",
    "heap: alloc/free ok",
    "AOS TICK TEST COMPLETE",
    "fault test: raising #UD",
    " handled",
)


def store_expectations(count: int = 64) -> dict | None:
    """The hashes a correct guest must print, computed independently here."""
    if storeref is None:
        return None
    sectors = storeref.DATA_START + count * storeref.REC_SECTORS
    image = bytearray(sectors * storeref.SECTOR)
    image[0:storeref.SECTOR] = storeref.superblock(count)
    for index in range(count):
        start = storeref.lba_of(index) * storeref.SECTOR
        image[start:start + storeref.REC_SIZE] = storeref.record(
            index, index + 1, storeref.payload_for(index, storeref.MAX_PAYLOAD))
    return {
        "superblock": fnv1a32(bytes(image[0:0x18])),
        "rec0": fnv1a32(storeref.payload_for(0, storeref.MAX_PAYLOAD)),
        "rec63": fnv1a32(storeref.payload_for(count - 1, storeref.MAX_PAYLOAD)),
        "region": fnv1a32(bytes(image)),
    }


def newest_log(base: str) -> str | None:
    logs = glob.glob(os.path.join(base, "logs", "*.log"))
    return max(logs, key=os.path.getmtime) if logs else None


def check(path: str) -> bool:
    print(f"\n{path}")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"  [FAIL] cannot read log: {exc}")
        return False

    print("  --- log ---")
    for line in text.splitlines():
        print(f"  | {line}")
    print("  -----------")

    reached = [marker for marker in STAGE_MARKERS if marker in text]
    if reached:
        print(f"  last stage marker: {reached[-1]!r}")

    if "store: replay identical" in text:
        milestone = 10
    elif "virtio: sector2048 fnv=" in text:
        milestone = 9
    elif "virtio: writeback fnv=" in text:
        milestone = 8
    elif "virtio: sector0 fnv=" in text:
        milestone = 7
    elif "anvil:" in text or "c-kernel:" in text:
        milestone = 4
    elif "exception 0x" in text or "fault test: raising #UD" in text:
        milestone = 3
    elif "paging: " in text or "e820: " in text:
        milestone = 2
    elif "AOS HALT" in text and not reached and "tick " not in text:
        milestone = 0
    else:
        milestone = 1
    print(f"  milestone detected: {milestone}")

    ok = True

    def require(markers: tuple[str, ...]) -> None:
        nonlocal ok
        for marker in markers:
            hit = marker in text
            print(f"  [{'PASS' if hit else 'FAIL'}] {marker!r}")
            ok = ok and hit

    def check_ticks() -> None:
        nonlocal ok
        observed = [int(value) for value in TICK_LINE.findall(text)]
        print(f"  ticks observed: {observed}")
        expected = list(range(1, 11))
        if observed != expected:
            print(f"  [FAIL] expected ticks {expected}")
            ok = False
        else:
            print("  [PASS] ticks 1..10 in order")

    if milestone >= 4:
        # The C kernel must reach feature parity with the milestone-3 asm
        # kernel: memory map, MMU, timer interrupts and exception handling.
        require(M4_MARKERS)
        require(M2_MARKERS)
        if "paging: REMAP FAILED" in text:
            print("  [FAIL] translation is not working - the remap check failed")
            ok = False
        if "heap: " in text:
            require(("heap: alloc/free ok",))
        check_ticks()
        require(M3_MARKERS)

        if "store: formatted" in text:
            # M10: records on the bare disk, no partition table, no filesystem.
            # Every hash is compared with the reference implementation in
            # tools/rawstore_check.py, not with a second copy of the kernel's
            # own assumptions.
            require(M10_MARKERS)
            if text.count("store: verify ok 64/64") < 2:
                print("  [FAIL] verification must pass both before and after the "
                      "corruption test")
                ok = False
            expected = store_expectations()
            for label, pattern in (
                    ("rec0", r"store: rec0 fnv=0x([0-9a-fA-F]{8})"),
                    ("rec63", r"store: rec63 fnv=0x([0-9a-fA-F]{8})"),
                    ("superblock", r"store: superblock fnv=0x([0-9a-fA-F]{8})"),
                    ("region", r"store: region fnv=0x([0-9a-fA-F]{8})")):
                match = re.search(pattern, text)
                if not match:
                    print(f"  [FAIL] no {label} hash in the log")
                    ok = False
                    continue
                got = int(match.group(1), 16)
                if expected is None:
                    # Without the reference there is no independent check at all,
                    # so this must fail rather than quietly pass.
                    print(f"  [FAIL] {label}: reference implementation missing, "
                          f"so the hash cannot be verified")
                    ok = False
                    continue
                same = got == expected[label]
                print(f"  [{'PASS' if same else 'FAIL'}] {label} 0x{got:08x} "
                      f"(host expects 0x{expected[label]:08x})")
                ok = ok and same

        if milestone >= 7:
            require(M7_MARKERS)
            match = re.search(r"virtio: sector0 fnv=0x([0-9a-fA-F]{8})", text)
            image = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "images", "testdisk.img")
            if not match:
                print("  [FAIL] no sector-0 hash in the log")
                ok = False
            elif os.path.isfile(image):
                with open(image, "rb") as handle:
                    expected = fnv1a32(handle.read(512))
                got = int(match.group(1), 16)
                same = got == expected
                print(f"  [{'PASS' if same else 'FAIL'}] sector0 0x{got:08x} "
                      f"(host expects 0x{expected:08x})")
                ok = ok and same
            else:
                print("  [WARN] testdisk.img missing - hash not cross-checked")

        if milestone >= 8:
            require(M8_MARKERS)
            pattern = bytes((i * 7 + 3) & 0xFF for i in range(512))
            expected_wb = fnv1a32(pattern)
            match_wb = re.search(r"virtio: writeback fnv=0x([0-9a-fA-F]{8})", text)
            if match_wb:
                got_wb = int(match_wb.group(1), 16)
                same_wb = got_wb == expected_wb
                print(f"  [{'PASS' if same_wb else 'FAIL'}] writeback 0x{got_wb:08x} "
                      f"(host expects 0x{expected_wb:08x})")
                ok = ok and same_wb
            else:
                print("  [FAIL] no writeback hash in the log")
                ok = False

        if milestone >= 9:
            require(M9_MARKERS)
            match_lba = re.search(r"virtio: sector2048 fnv=0x([0-9a-fA-F]{8})", text)
            if match_lba and os.path.isfile(image):
                with open(image, "rb") as handle:
                    handle.seek(LBA_MARKER * 512)
                    expected_lba = fnv1a32(handle.read(512))
                got_lba = int(match_lba.group(1), 16)
                same_lba = got_lba == expected_lba
                print(f"  [{'PASS' if same_lba else 'FAIL'}] lba {LBA_MARKER}: "
                      f"0x{got_lba:08x} (host expects 0x{expected_lba:08x})")
                ok = ok and same_lba
            else:
                print("  [FAIL] no sector-2048 hash to check")
                ok = False

        if not ok and reached:
            print(f"  hint: execution got as far as {reached[-1]!r} and then stopped")
        return ok

    require(M0_MARKERS)

    if milestone >= 1:
        require(M1_MARKERS)

        check_ticks()
    else:
        hit = "AOS HALT" in text
        print(f"  [{'PASS' if hit else 'FAIL'}] 'AOS HALT'")
        ok = ok and hit

    if milestone >= 2:
        require(M2_MARKERS)
        if "paging: REMAP FAILED" in text:
            print("  [FAIL] translation is not working - the remap check failed")
            ok = False

    if milestone >= 3:
        require(M3_MARKERS)

    if not ok and reached:
        print(f"  hint: execution got as far as {reached[-1]!r} and then stopped")
    return ok


def main(argv: list[str]) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    if argv:
        paths = argv
    else:
        found = newest_log(here)
        paths = [found] if found else []

    if not paths:
        print("No logs found in", os.path.join(here, "logs"))
        print("Run run_vm.bat first.")
        return 2

    passed = all([check(path) for path in paths])
    print("\nRESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
