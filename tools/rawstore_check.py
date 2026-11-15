#!/usr/bin/env python3
"""Reference implementation and verifier for the M10 raw record store.

This exists *before* the kernel code, deliberately. A checker written afterwards tends to
agree with whatever the implementation did; this one defines the format independently, so
the guest has to match it rather than the other way round.

    py rawstore_check.py --build img --records 64     # make an image to spec
    py rawstore_check.py --verify img                 # check every record
    py rawstore_check.py --corrupt img --record 32    # negative test: break one byte
    py rawstore_check.py --orphan img                 # write an uncommitted record
    py rawstore_check.py --selftest                   # build, verify, corrupt, verify

The self-test is the important mode. It asserts that the verifier *passes* a good image and
*fails* a corrupted one. A verifier that cannot fail proves nothing at all.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

SECTOR = 512
TOTAL_SECTORS = 16384                 # 8 MiB, as mkdisk.py builds
DATA_START = 16
REC_SECTORS = 4
REC_SIZE = REC_SECTORS * SECTOR       # 2048
HEADER = 32
MAX_PAYLOAD = REC_SIZE - HEADER       # 2016
CAPACITY = (TOTAL_SECTORS - DATA_START) // REC_SECTORS   # 4088

SB_MAGIC = b"ANVILRS1"
REC_MAGIC = b"REC1"
VERSION = 1


def fnv1a(data: bytes) -> int:
    h = 0x811C9DC5
    for byte in data:
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def payload_for(index: int, length: int) -> bytes:
    """The LCG from the specification. Must stay identical to the C version."""
    state = (0x12345678 + index) & 0xFFFFFFFF
    out = bytearray(length)
    for i in range(length):
        state = (state * 1103515245 + 12345) & 0xFFFFFFFF
        out[i] = (state >> 16) & 0xFF
    return bytes(out)


def lba_of(index: int) -> int:
    return DATA_START + index * REC_SECTORS


def superblock(count: int) -> bytes:
    body = struct.pack("<8sIIII", SB_MAGIC, VERSION, REC_SECTORS, DATA_START, count)
    block = body + struct.pack("<I", fnv1a(body))
    return block.ljust(SECTOR, b"\0")


def record(index: int, seq: int, payload: bytes) -> bytes:
    head = struct.pack("<4sIIII", REC_MAGIC, index, len(payload), fnv1a(payload), seq)
    head += struct.pack("<I", fnv1a(head))
    return (head.ljust(HEADER, b"\0") + payload).ljust(REC_SIZE, b"\0")


def build(path: str, count: int, seq_base: int = 1) -> int:
    if count > CAPACITY:
        print(f"[store] {count} records exceeds capacity {CAPACITY}")
        return 2
    image = bytearray(TOTAL_SECTORS * SECTOR)
    image[0:SECTOR] = superblock(count)
    for index in range(count):
        start = lba_of(index) * SECTOR
        image[start:start + REC_SIZE] = record(index, seq_base + index,
                                              payload_for(index, MAX_PAYLOAD))
    with open(path, "wb") as handle:
        handle.write(image)
    print(f"[store] built {path}: {count} record(s), capacity {CAPACITY}")
    print(f"[store] superblock fnv=0x{fnv1a(image[0:0x18]):08x}")
    return 0


def read_superblock(image: bytes) -> tuple[int, bool]:
    block = image[:SECTOR]
    body = block[:0x18]
    stored = struct.unpack("<I", block[0x18:0x1C])[0]
    magic, version, rec_sectors, data_start, count = struct.unpack("<8sIIII", body)
    ok = (magic == SB_MAGIC and version == VERSION and rec_sectors == REC_SECTORS
          and data_start == DATA_START and stored == fnv1a(body))
    print(f"store: superblock count={count} fnv=0x{stored:08x} "
          f"{'ok' if ok else 'INVALID'}")
    return count, ok


def verify(path: str) -> int:
    with open(path, "rb") as handle:
        image = handle.read()
    expected = TOTAL_SECTORS * SECTOR
    if len(image) != expected:
        print(f"store: verify FAIL wrong image size {len(image)} != {expected}")
        return 1

    count, ok = read_superblock(image)
    if not ok:
        print("store: verify FAIL superblock")
        return 1
    if count > CAPACITY:
        print(f"store: verify FAIL count {count} exceeds capacity {CAPACITY}")
        return 1

    bad = 0
    for index in range(count):
        start = lba_of(index) * SECTOR
        block = image[start:start + REC_SIZE]
        magic, rec_index, length, payload_fnv, seq, head_fnv = struct.unpack(
            "<4sIIIII", block[:0x18])
        head_ok = (magic == REC_MAGIC and rec_index == index and length <= MAX_PAYLOAD
                   and head_fnv == fnv1a(block[:0x14]))
        payload = block[HEADER:HEADER + length] if length <= MAX_PAYLOAD else b""
        body_ok = head_ok and payload_fnv == fnv1a(payload)
        if not body_ok:
            print(f"store: verify FAIL rec {index}")
            bad += 1
        elif index in (0, count - 1):
            print(f"store: rec{index} fnv=0x{payload_fnv:08x} seq={seq}")

    # a record beyond the count is an orphan: written, never committed, must be ignored
    orphan = (count < CAPACITY
              and image[lba_of(count) * SECTOR:lba_of(count) * SECTOR + 4] == REC_MAGIC)
    if orphan:
        print(f"store: orphan ignored at rec {count}")

    if bad:
        print(f"store: verify FAIL {bad} of {count} record(s)")
        return 1
    print(f"store: verify ok {count}/{count}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="M10 raw store reference tool")
    parser.add_argument("--build", metavar="IMG")
    parser.add_argument("--verify", metavar="IMG")
    parser.add_argument("--corrupt", metavar="IMG")
    parser.add_argument("--orphan", metavar="IMG")
    parser.add_argument("--record", type=int, default=0)
    parser.add_argument("--records", type=int, default=64)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "selftest.img")
        print("== 1. build and verify a good image ==")
        if build(path, 64) != 0 or verify(path) != 0:
            print("SELFTEST FAILED at the good-image step")
            return 1
        print("\n== 2. corrupt one payload byte, verifier must fail ==")
        with open(path, "r+b") as handle:
            target = lba_of(32) * SECTOR + HEADER + 100
            handle.seek(target)
            byte = handle.read(1)
            handle.seek(target)
            handle.write(bytes([byte[0] ^ 0xFF]))
        if verify(path) == 0:
            print("SELFTEST FAILED: corrupted image passed verification")
            return 1
        print("\n== 3. rebuild, add an orphan record, count must not change ==")
        build(path, 64)
        with open(path, "r+b") as handle:
            handle.seek(lba_of(64) * SECTOR)
            handle.write(record(64, 65, payload_for(64, MAX_PAYLOAD)))
        if verify(path) != 0:
            print("SELFTEST FAILED: orphan should be ignored")
            return 1
        print("\nSELFTEST PASS - the verifier accepts good images and rejects bad ones")
        return 0

    if args.build:
        return build(args.build, args.records)
    if args.verify:
        return verify(args.verify)
    if args.corrupt:
        with open(args.corrupt, "r+b") as handle:
            handle.seek(lba_of(args.record) * SECTOR + HEADER + 7)
            byte = handle.read(1)
            handle.seek(lba_of(args.record) * SECTOR + HEADER + 7)
            handle.write(bytes([byte[0] ^ 0xFF]))
        print(f"[store] corrupted record {args.record}")
        return 0
    if args.orphan:
        with open(args.orphan, "r+b") as handle:
            handle.seek(lba_of(args.record) * SECTOR)
            handle.write(record(args.record, 9999, payload_for(args.record, MAX_PAYLOAD)))
        print(f"[store] wrote uncommitted record {args.record}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
