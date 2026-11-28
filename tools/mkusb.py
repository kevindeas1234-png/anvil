#!/usr/bin/env python
"""Build images/anvil-usb.img - a bootable stick image with a REAL partition table.

The floppy image puts the kernel at LBA 1 and has no partition table. Some
firmware will not boot a "USB-HDD" that lacks a valid partition entry: it
rejects the device, falls through the boot order, and loops. SeaBIOS is lenient,
which is why this never showed up in QEMU.

So the USB image is the same boot code with partition 1 declared, and the kernel
moved to the start of that partition. stage1 reads the table it was booted with
and finds the kernel either way - no separate build, no second constant.

    py tools\\mkusb.py [size_mb]

Default 64 MiB, which some firmware requires before it will consider a device
bootable at all.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

IMAGE = os.path.join(ROOT, "images", "anvil.img")
OUT = os.path.join(ROOT, "images", "anvil-usb.img")
STAGE1 = os.path.join(ROOT, "kernel", "stage1.asm")


def kernel_sectors() -> int:
    """Read KERNEL_SECTORS from stage1.asm.

    The floppy image is padded to a full 1.44 MB, so "everything after the boot
    sector" is 1.4 MB of mostly zeros, not the kernel. And a second copy of the
    sector count here would drift from the one the boot loader uses - which is
    the pairing rule the contract warns about.
    """
    with open(STAGE1, encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"\s*KERNEL_SECTORS\s+equ\s+(\d+)", line)
            if match:
                return int(match.group(1))
    raise SystemExit("KERNEL_SECTORS not found in stage1.asm")

SECTOR = 512
PART_START = 2048          # conventional: 1 MiB in
PART_SECTORS = 2048        # 1 MiB of kernel space
SIZE_MB = int(sys.argv[1]) if len(sys.argv) > 1 else 64


def main() -> int:
    with open(IMAGE, "rb") as handle:
        image = handle.read()

    boot = bytearray(image[:SECTOR])

    sectors = kernel_sectors()
    if sectors > PART_SECTORS:
        raise SystemExit(f"KERNEL_SECTORS is {sectors}, partition holds {PART_SECTORS}")
    kernel = image[SECTOR:SECTOR + sectors * SECTOR]

    if not kernel.strip(b"\x00"):
        raise SystemExit("kernel region is empty - build the image first")

    # Partition entry 1 at offset 446: status, CHS start, type, CHS end, LBA, count
    struct.pack_into("<B3sB3sII", boot, 446,
                     0x80,               # active
                     b"\xFE\xFF\xFF",    # CHS start: "ignore", use LBA
                     0x0C,               # FAT32 LBA - the type most firmware expects
                     b"\xFE\xFF\xFF",    # CHS end: same
                     PART_START,
                     PART_SECTORS)
    boot[510:512] = b"\x55\xAA"

    total = SIZE_MB * 1024 * 1024
    blob = bytearray(total)
    blob[0:SECTOR] = boot
    offset = PART_START * SECTOR
    blob[offset:offset + len(kernel)] = kernel

    with open(OUT, "wb") as handle:
        handle.write(blob)

    digest = hashlib.sha256(blob).hexdigest()
    print(f"  boot sector  : {len(boot)} bytes, partition 1 at LBA {PART_START}, "
          f"{PART_SECTORS} sectors")
    print(f"  kernel       : {len(kernel):,} bytes at LBA {PART_START}")
    print(f"  image        : {len(blob):,} bytes ({SIZE_MB} MiB)")
    print(f"  sha256       : {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
