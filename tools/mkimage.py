"""Build the bootable floppy image from a boot sector and a flat kernel.

    py tools\\mkimage.py build\\boot.bin build\\kernel.bin images\\aos.img

Layout of the result:
    bytes 0..511        boot sector (must be 512 bytes, ends with 0xAA55)
    bytes 512..N        kernel, loaded by stage 1 at physical 0x10000
    bytes N..1474559    zero padding to a 1.44 MB floppy
"""

from __future__ import annotations

import os
import sys

TOTAL_SIZE = 1474560
BOOT_SIZE = 512
KERNEL_LIMIT = 64 * 512          # must match KERNEL_SECTORS in stage1.asm


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: mkimage.py <boot.bin> <kernel.bin> <out.img>")
        return 2

    boot_path, kernel_path, out_path = argv[0], argv[1], argv[2]

    try:
        with open(boot_path, "rb") as handle:
            boot = handle.read()
        with open(kernel_path, "rb") as handle:
            kernel = handle.read()
    except OSError as exc:
        print(f"[mkimage] cannot read input: {exc}")
        return 1

    if len(boot) != BOOT_SIZE:
        print(f"[mkimage] boot sector is {len(boot)} bytes, expected {BOOT_SIZE}")
        return 1
    if boot[510:512] != b"\x55\xaa":
        print("[mkimage] boot sector is missing the 0xAA55 signature")
        return 1
    if not kernel:
        print("[mkimage] kernel binary is empty")
        return 1
    if len(kernel) > KERNEL_LIMIT:
        print(f"[mkimage] kernel is {len(kernel)} bytes, limit is {KERNEL_LIMIT}")
        return 1

    image = boot + kernel + bytes(TOTAL_SIZE - BOOT_SIZE - len(kernel))
    with open(out_path, "wb") as handle:
        handle.write(image)

    print(f"[mkimage] boot={len(boot)} kernel={len(kernel)} "
          f"image={len(image)} free_in_kernel_area={KERNEL_LIMIT - len(kernel)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
