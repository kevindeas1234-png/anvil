#!/usr/bin/env python3
"""Write a policy table into an ANVIL disk image (sector 1).

The kernel reads this sector at startup. It is how a decision made *outside* the
kernel - by a person, a script, or an agent - changes how much work the machine
does, without a rebuild and without touching a single mechanism.

    py setpolicy.py --image images/testdisk.img --chunk 4 --flush 8
    py setpolicy.py --image images/testdisk.img --clear
    py setpolicy.py --image images/testdisk.img --show
    py setpolicy.py --selfcheck

Values are clamped by the kernel as well, deliberately: a bad policy must
degrade to default behaviour, never to a wrong result.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

SECTOR = 512
POLICY_LBA_OFFSET_FROM_END = 1   # the last sector of the device
MAGIC = b"ANVLPOL1"


def fnv1a(data: bytes) -> int:
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def build(chunk: int, flush: int, pack_threshold: int = 0) -> bytes:
    body = struct.pack("<8sHHHH", MAGIC, chunk, flush, pack_threshold, 0)
    block = body + struct.pack("<I", fnv1a(body))
    return block.ljust(SECTOR, b"\0")


def parse(block: bytes) -> dict | None:
    magic, chunk, flush, pack, _reserved = struct.unpack("<8sHHHH", block[:16])
    stored = struct.unpack("<I", block[16:20])[0]
    if magic != MAGIC or stored != fnv1a(block[:16]):
        return None
    return {"chunk": chunk, "flush": flush, "pack_threshold": pack}


def policy_lba(image: str) -> int:
    """The policy lives in the device's last sector, outside the hashed data
    region, so it cannot change the region hash and needs no exception there."""
    return os.path.getsize(image) // SECTOR - POLICY_LBA_OFFSET_FROM_END


def write(image: str, block: bytes) -> int:
    lba = policy_lba(image)
    if lba < 16:
        print(f"[policy] {image} is too small to hold a policy sector")
        return 2
    with open(image, "r+b") as handle:
        handle.seek(lba * SECTOR)
        handle.write(block)
    return 0


def show(image: str) -> int:
    with open(image, "rb") as handle:
        handle.seek(policy_lba(image) * SECTOR)
        block = handle.read(SECTOR)
    parsed = parse(block)
    if parsed is None:
        print("[policy] no valid policy table (the kernel will use its defaults)")
        return 1
    print(f"[policy] chunk={parsed['chunk']} flush={parsed['flush']} "
          f"pack_threshold={parsed['pack_threshold']}")
    return 0


def selfcheck() -> int:
    """Prove the table can be read back, and that corruption is refused."""
    good = build(4, 8)
    parsed = parse(good)
    print(f"1. round trip        -> {parsed}")
    if parsed != {"chunk": 4, "flush": 8, "pack_threshold": 0}:
        print("SELFCHECK FAILED: round trip did not match")
        return 1
    broken = bytearray(good)
    broken[10] ^= 0xFF
    if parse(bytes(broken)) is not None:
        print("SELFCHECK FAILED: a corrupted table was accepted")
        return 1
    print("2. corrupted rejected -> refused, as it must be")
    print("SELFCHECK PASS")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="write an ANVIL policy table")
    parser.add_argument("--image", default=r"D:\OS\images\testdisk.img")
    parser.add_argument("--chunk", type=int, default=4)
    parser.add_argument("--flush", type=int, default=1)
    parser.add_argument("--pack-threshold", type=int, default=0)
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return selfcheck()
    if args.show:
        return show(args.image)
    if args.clear:
        code = write(args.image, bytes(SECTOR))
        print(f"[policy] cleared in {args.image}")
        return code
    if not os.path.isfile(args.image):
        print(f"[policy] no such image: {args.image}")
        return 2
    block = build(args.chunk, args.flush, args.pack_threshold)
    code = write(args.image, block)
    if code == 0:
        print(f"[policy] chunk={args.chunk} flush={args.flush} written to {args.image}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
