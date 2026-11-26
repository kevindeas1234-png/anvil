#!/usr/bin/env python
"""Capture the ANVIL VGA text console as PNG frames.

The kernel writes to COM1 for the test harness and to 0xB8000 for humans. This
drives QEMU's monitor, asks it for a screendump every so often, and converts the
PPM dumps to PNG with zlib so the frames are small enough to mail.

    py D:\\AOS\\tools\\capture_boot.py [frames] [interval_seconds] [outdir]

Defaults: 60 frames, 0.10 s apart, D:\\ANVIL_capture\\frames

Nothing here touches the kernel image: it boots the same floppy with -snapshot,
so every write is discarded when the VM quits. No NIC, same as the test harness.
"""

import json
import os
import struct
import subprocess
import sys
import time
import zlib

QEMU = r"C:\Program Files\qemu\qemu-system-x86_64.exe"
ROOT = r"D:\OS"

FRAMES   = int(sys.argv[1]) if len(sys.argv) > 1 else 200
INTERVAL = float(sys.argv[2]) if len(sys.argv) > 2 else 0.06
OUTDIR   = sys.argv[3] if len(sys.argv) > 3 else r"D:\ANVIL_capture\frames"

PROMPT = b"(qemu) "


def read_ppm(path):
    """Return (width, height, [row bytes]) from a binary PPM (P6)."""
    with open(path, "rb") as fh:
        data = fh.read()
    fields, pos = [], 0
    while len(fields) < 4:
        # skip whitespace and comments
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        fields.append(data[start:pos])
    pos += 1                                     # single whitespace after maxval
    magic, wstr, hstr, maxval = fields
    if magic != b"P6" or maxval != b"255":
        raise ValueError("not an 8-bit binary PPM: %r %r" % (magic, maxval))
    w, h = int(wstr), int(hstr)
    stride = w * 3
    rows = [data[pos + y * stride: pos + (y + 1) * stride] for y in range(h)]
    if any(len(r) != stride for r in rows):
        raise ValueError("truncated PPM")
    return w, h, rows


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + r for r in rows)     # filter type 0 per scanline
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)


def main():
    image = os.path.join(ROOT, "images", "anvil.img")
    disk = os.path.join(ROOT, "images", "testdisk.img")
    for p in (QEMU, image, disk):
        if not os.path.exists(p):
            sys.exit("missing: %s" % p)

    os.makedirs(OUTDIR, exist_ok=True)
    for stale in os.listdir(OUTDIR):
        if stale.endswith(".ppm") or stale.endswith(".png"):
            os.remove(os.path.join(OUTDIR, stale))

    cmd = [QEMU,
           "-drive", "format=raw,if=floppy,file=%s" % image,
           "-drive", "file=%s,format=raw,if=virtio" % disk,
           "-snapshot", "-nic", "none",
           "-display", "none",
           "-monitor", "stdio",
           "-device", "isa-debug-exit,iobase=0x501,iosize=0x04",
           "-m", "256", "-no-reboot"]

    print("[capture] booting: %d frames, %.2f s apart" % (FRAMES, INTERVAL))
    vm = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, bufsize=0)

    def until_prompt():
        buf = b""
        while not buf.endswith(PROMPT):
            ch = vm.stdout.read(1)
            if not ch:
                raise RuntimeError("QEMU monitor went away")
            buf += ch
        return buf

    until_prompt()
    made = 0          # frames written
    times = []        # wall-clock seconds since boot, one per frame
    start = time.time()
    w = h = 0
    try:
        for i in range(FRAMES):
            ppm = os.path.join(OUTDIR, "frame_%04d.ppm" % i)
            try:
                vm.stdin.write(("screendump %s\n" % ppm.replace("\\", "/")).encode())
                vm.stdin.flush()
            except OSError:
                # The kernel exits the VM itself (isa-debug-exit) when its tests
                # finish. That is a normal ending, not a failure: stop cleanly.
                print("[capture] VM exited after %d frames" % made)
                break
            try:
                until_prompt()
            except RuntimeError:
                print("[capture] monitor closed after %d frames" % made)
                break
            for _ in range(50):                 # screendump is synchronous, but be patient
                if os.path.exists(ppm) and os.path.getsize(ppm) > 1024:
                    break
                time.sleep(0.02)
            if not os.path.exists(ppm):
                print("[capture] frame %d: no file written, stopping" % i)
                break
            w, h, rows = read_ppm(ppm)
            write_png(os.path.join(OUTDIR, "frame_%04d.png" % i), w, h, rows)
            os.remove(ppm)
            times.append(round(time.time() - start, 4))
            made += 1
            time.sleep(INTERVAL)
    finally:
        try:
            vm.stdin.write(b"quit\n")
            vm.stdin.flush()
        except Exception:
            pass
        try:
            vm.wait(timeout=5)
        except subprocess.TimeoutExpired:
            vm.kill()

    # Frame timing is measured, not assumed: the control loop costs real time
    # and an assumed interval would make the animation drift from reality.
    with open(os.path.join(OUTDIR, "timing.json"), "w") as fh:
        json.dump({"frames": made, "width": w, "height": h, "times": times}, fh, indent=1)

    print("[capture] %d frames at %dx%d -> %s" % (made, w, h, OUTDIR))
    if len(times) > 2:
        gaps = [b - a for a, b in zip(times, times[1:])]
        print("[capture] frame gap: avg %.3f s, min %.3f s, max %.3f s"
              % (sum(gaps) / len(gaps), min(gaps), max(gaps)))
    if made:
        first = os.path.getsize(os.path.join(OUTDIR, "frame_0000.png"))
        print("[capture] first frame %d bytes; check it is not blank before encoding" % first)


if __name__ == "__main__":
    main()
