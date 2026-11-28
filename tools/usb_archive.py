#!/usr/bin/env python
"""Archive every build that could be put on a stick, with the hashes to prove it.

A 64 MiB USB image is mostly padding. What makes it reproducible is much
smaller: the 1.44 MB floppy image, the commit it came from, and the tool that
expands it (tools/mkusb.py, which git versions). So this archive keeps the small
artefact and the hashes of the big ones - enough to rebuild byte-for-byte, and
enough to tell whether a rebuild matches.

    py tools\\usb_archive.py            # archive the current build
    py tools\\usb_archive.py --verify   # check every archived build still hashes

Nothing here is written into the repository: images are generated, not source.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ARCHIVE = os.environ.get("ANVIL_USB_ARCHIVE", r"D:\ANVIL_usb_archive")
MANIFEST = os.path.join(ARCHIVE, "MANIFEST.md")

ANVIL_IMG = os.path.join(ROOT, "images", "anvil.img")
USB_IMG = os.path.join(ROOT, "images", "anvil-usb.img")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def commit() -> tuple[str, str]:
    try:
        full = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        return full[:7], subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%s"],
                                        capture_output=True, text=True,
                                        check=True).stdout.strip()
    except Exception:
        return "unknown", "(no git)"


def ensure_manifest() -> None:
    os.makedirs(ARCHIVE, exist_ok=True)
    if not os.path.exists(MANIFEST):
        with open(MANIFEST, "w", encoding="utf-8") as handle:
            handle.write("# ANVIL build archive\n\n"
                         "Each row is a build that could be written to a stick. The floppy image is\n"
                         "kept because it is the input everything else is derived from; the USB image\n"
                         "hash is recorded so a rebuild can be checked against it.\n\n"
                         "| Built | Commit | Kernel | anvil.img sha256 | anvil-usb.img sha256 | Note |\n"
                         "|---|---|---|---|---|---|\n")


def verify() -> int:
    if not os.path.exists(MANIFEST):
        print(f"  no manifest at {MANIFEST}")
        return 1
    bad = 0
    checked = 0
    for line in open(MANIFEST, encoding="utf-8"):
        if not line.startswith("| 20"):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        built, commit, kernel, floppy_sha, _usb_sha, _note = parts
        name = os.path.join(ARCHIVE, f"{commit}-{kernel}-{floppy_sha[:8]}.img")
        checked += 1
        if not os.path.exists(name):
            print(f"  [FAIL] missing archive file for {commit}: {name}")
            bad += 1
        elif sha256(name) != floppy_sha:
            print(f"  [FAIL] hash mismatch for {commit}")
            bad += 1
    print(f"  verified {checked - bad}/{checked} archived builds")
    return 0 if bad == 0 else 1


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    if not os.path.exists(ANVIL_IMG):
        print(f"  no image at {ANVIL_IMG} - run test.bat first")
        return 1
    ensure_manifest()

    short, subject = commit()
    floppy = sha256(ANVIL_IMG)
    usb = sha256(USB_IMG) if os.path.exists(USB_IMG) else "(not built)"
    try:
        kernel = str(os.path.getsize(os.path.join(ROOT, "build", "kernel.bin")))
    except OSError:
        kernel = "?"

    stamp = time.strftime("%Y-%m-%d %H:%M")
    name = f"{short}-{kernel}-{floppy[:8]}.img"
    shutil.copy2(ANVIL_IMG, os.path.join(ARCHIVE, name))

    with open(MANIFEST, "a", encoding="utf-8") as handle:
        handle.write(f"| {stamp} | {short} | {kernel} | {floppy} | {usb} | {subject} |\n")

    print(f"  archived : {name}")
    print(f"  commit   : {short}  ({subject[:60]})")
    print(f"  kernel   : {kernel} bytes")
    print(f"  floppy   : {floppy}")
    print(f"  usb image: {usb}")
    print(f"  manifest : {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
