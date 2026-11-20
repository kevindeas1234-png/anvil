#!/usr/bin/env python3
"""Create the deterministic disk image ANVIL reads from.

Sector 0 is a known pattern, so the kernel's hash can be checked against the
host's description of the same bytes. Everything after it is zero.

    py tools\\mkdisk.py images\\testdisk.img [--size-mib 8]
"""

from __future__ import annotations

import argparse
import hashlib
import os

SECTOR = 512
MAGIC = b"ANVIL VIRTIO TEST SECTOR 0"
LBA_MARKER = 2048                 # the "read an arbitrary sector" target


def build_sector() -> bytes:
    """512 deterministic bytes: a header, a counter, then a filler pattern."""
    head = MAGIC.ljust(64, b"\x00")
    counter = bytes(i & 0xFF for i in range(64))
    filler = hashlib.blake2b(head + counter).digest()
    block = head + counter + filler * ((SECTOR - 128 + len(filler) - 1) // len(filler))
    return block[:SECTOR]


def build_marker() -> bytes:
    """Sector 2048: a header plus a pattern, so a wrong LBA cannot pass."""
    head = b"ANVIL LBA 2048 MARKER".ljust(64, b"\x00")
    pattern = bytes((i * 13 + 7) & 0xFF for i in range(SECTOR - 64))
    return head + pattern


def fnv1a32(data: bytes) -> int:
    h = 2166136261
    for byte in data:
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="build the virtio test disk")
    parser.add_argument("path")
    parser.add_argument("--size-mib", type=int, default=8)
    args = parser.parse_args(argv)

    size = args.size_mib * 1024 * 1024
    sector = build_sector()
    os.makedirs(os.path.dirname(os.path.abspath(args.path)), exist_ok=True)

    marker = build_marker()
    with open(args.path, "wb") as handle:
        handle.write(sector)
        handle.write(bytes(size - SECTOR))
        handle.seek(LBA_MARKER * SECTOR)
        handle.write(marker)

    print(f"[mkdisk] {args.path}  {size // (1024 * 1024)} MiB")
    print(f"[mkdisk] sector0 fnv1a32 = 0x{fnv1a32(sector):08x}")
    print(f"[mkdisk] lba {LBA_MARKER} fnv1a32 = 0x{fnv1a32(marker):08x}")
    print(f"[mkdisk] sector0 sha256  = {hashlib.sha256(sector).hexdigest()[:32]}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
