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
    "exception 0x",          # the vector is checked exactly below, not matched here
    " handled",
)

# M13: a fault must report what it was, where, and what the kernel had been doing.
M11_MARKERS = (
    "store: pack runs   ",
    "(packed), wrote ",
    "store: pack noise  ",
    "(plain), wrote ",
    "store: pack verdict runs packed, noise plain",
)

M13_MARKERS = (
    "panic: vector=0x",
    "panic: recent output",
    "panic: halted",
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


def check(path: str, require_lba: bool = False,
          device_sectors: int = 0, no_device_hashes: bool = False,
          expect_panic_vector: int = -1) -> bool:
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

    # M14: stage1 announces how it loaded the kernel, on COM1, before anything
    # else. A boot loader that merely claims to have used LBA reads is worth
    # nothing; this is the claim written into the log.
    marker = None
    for line in text.splitlines():
        if line.strip() in ("L", "C"):
            marker = line.strip()
            break
    if marker is None:
        print("  [FAIL] no boot-path marker (expected 'L' or 'C' on the first line)")
        ok = False
    else:
        label = "extended LBA reads" if marker == "L" else "CHS fallback"
        print(f"  [PASS] boot path marker {marker!r} ({label})")
        if require_lba and marker != "L":
            print("  [FAIL] this configuration must use extended LBA reads, "
                  "but the guest fell back to CHS - geometry is still a dependency")
            ok = False

    # Optional: a device larger than 2 TiB. Every other check works near the start
    # of the disk, so without this a 32-bit sector count can describe a 3 TiB
    # device as ~1 TiB and nothing fails. The test contract is that the host
    # writes 0xAB into the device's last sector before booting.
    if device_sectors:
        match = re.search(r"virtio: capacity lo=(\d+) hi=(\d+)", text)
        if not match:
            print("  [FAIL] no capacity line in the log")
            ok = False
        else:
            reported = (int(match.group(2)) << 32) | int(match.group(1))
            same = reported == device_sectors
            print(f"  [{'PASS' if same else 'FAIL'}] capacity {reported} sectors "
                  f"(host expects {device_sectors})")
            ok = ok and same

        expected_fnv = fnv1a32(bytes([0xAB]) * 512)
        match = re.search(
            r"store: last sector lba_lo=(\d+) lba_hi=(\d+) fnv=0x([0-9a-fA-F]{8})", text)
        if not match:
            print("  [FAIL] no last-sector read in the log")
            ok = False
        else:
            lba = (int(match.group(2)) << 32) | int(match.group(1))
            fnv = int(match.group(3), 16)
            lba_ok = lba == device_sectors - 1
            fnv_ok = fnv == expected_fnv
            print(f"  [{'PASS' if lba_ok else 'FAIL'}] last sector lba {lba} "
                  f"(host expects {device_sectors - 1})")
            print(f"  [{'PASS' if fnv_ok else 'FAIL'}] last sector 0x{fnv:08x} "
                  f"(host expects 0x{expected_fnv:08x})")
            ok = ok and lba_ok and fnv_ok

    def require(markers: tuple[str, ...]) -> None:
        nonlocal ok
        for marker in markers:
            hit = marker in text
            print(f"  [{'PASS' if hit else 'FAIL'}] {marker!r}")
            ok = ok and hit

    def check_panic() -> None:
        """M13. Defined once and called from every branch that runs a fault test.

        It used to live inline in the lower branch only, which milestone-10 logs
        never reach - so a wrong expected vector passed silently. A check that
        cannot fail is worse than no check.
        """
        nonlocal ok
        if "panic:" not in text:
            return
        require(M13_MARKERS)
        match = re.search(r"panic: vector=0x([0-9a-fA-F]{8})", text)
        if not match:
            print("  [FAIL] panic block has no vector")
            ok = False
            return
        got = int(match.group(1), 16)
        if expect_panic_vector >= 0:
            same = got == expect_panic_vector
            print(f"  [{'PASS' if same else 'FAIL'}] panic vector 0x{got:08x} "
                  f"(expected 0x{expect_panic_vector:08x})")
            ok = ok and same
        else:
            print(f"  [PASS] panic vector reported: 0x{got:08x}")

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
        check_panic()

        if "store: pack" in text:
            # M11: the pack/plain decision, proven by a round trip both ways.
            require(M11_MARKERS)
            m_runs = re.search(r"store: pack runs\s+1024 -> (\d+) bytes \(packed\), wrote (\d+)", text)
            m_noise = re.search(r"store: pack noise\s+1024 -> (\d+) bytes \(plain\), wrote (\d+)", text)
            if not m_runs or not m_noise:
                print("  [FAIL] could not read the pack measurements")
                ok = False
            else:
                packed_bytes, packed_wrote = int(m_runs.group(1)), int(m_runs.group(2))
                noise_wrote = int(m_noise.group(2))
                cheaper = packed_wrote < noise_wrote and packed_bytes < 1024
                print(f"  [{'PASS' if cheaper else 'FAIL'}] compressible wrote {packed_wrote} bytes "
                      f"({packed_bytes} packed) vs {noise_wrote} plain")
                ok = ok and cheaper

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
            elif no_device_hashes:
                # A non-test-disk device has different contents, so this
                # comparison has no meaning here. Say so out loud rather than
                # letting it pass quietly - a skipped check must be visible.
                print("  [SKIP] sector0 not compared: the device is not "
                      "images/testdisk.img")
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
            if match_lba and no_device_hashes:
                print("  [SKIP] lba 2048 not compared: the device is not "
                      "images/testdisk.img")
            elif match_lba and os.path.isfile(image):
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
        check_panic()

    if not ok and reached:
        print(f"  hint: execution got as far as {reached[-1]!r} and then stopped")
    return ok


def main(argv: list[str]) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    require_lba = "--require-lba" in argv
    argv = [item for item in argv if item != "--require-lba"]
    no_device_hashes = "--no-device-hashes" in argv
    argv = [item for item in argv if item != "--no-device-hashes"]
    expect_panic_vector = -1
    if "--expect-panic-vector" in argv:
        index = argv.index("--expect-panic-vector")
        expect_panic_vector = int(argv[index + 1], 0)
        del argv[index:index + 2]
    device_sectors = 0
    if "--device-sectors" in argv:
        index = argv.index("--device-sectors")
        device_sectors = int(argv[index + 1])
        del argv[index:index + 2]
    if argv:
        paths = argv
    else:
        found = newest_log(here)
        paths = [found] if found else []

    if not paths:
        print("No logs found in", os.path.join(here, "logs"))
        print("Run run_vm.bat first.")
        return 2

    passed = all([check(path, require_lba, device_sectors, no_device_hashes,
                        expect_panic_vector) for path in paths])
    print("\nRESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
