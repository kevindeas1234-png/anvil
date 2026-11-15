#!/usr/bin/env python3
"""Build a hardware inventory from the exported Windows driver store.

Reads every .inf under the driver folder (read-only), extracts the device
class, provider and hardware IDs, and classifies each package by how feasible
it would be to support from a small OS.

The point is not to collect drivers - .sys files cannot be loaded into ANVIL.
The point is to know which devices speak a published standard (supportable)
and which need vendor firmware and closed documentation (not supportable).

    py inventory.py                      # defaults to E:\\PC_Drivers
    py inventory.py --root E:\\PC_Drivers --out D:\\OS\\hardware_inventory.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter

HW_ID = re.compile(
    r"(PCI\\VEN_[0-9A-Fa-f]{4}&DEV_[0-9A-Fa-f]{4}"
    r"|USB\\VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}"
    r"|ACPI\\[A-Za-z0-9_.]+"
    r"|HID\\[A-Za-z0-9_.&]+)",
    re.IGNORECASE,
)

# class keyword -> (feasibility, why)
RULES = (
    (("display", "monitor"), "no", "GPU/Monitor: vendor firmware, closed drivers"),
    (("bluetooth",), "no", "Bluetooth: firmware plus a large stack"),
    (("printer",), "no", "Out of scope"),
    (("net",), "maybe", "Ethernet is standards-based; Wi-Fi needs firmware"),
    (("media", "audioendpoint"), "maybe", "HDA covers basic playback only"),
    (("scsiadapter", "hdc", "diskdrive", "nvme"), "yes", "AHCI/NVMe are published specs"),
    (("usb", "hidclass", "keyboard", "mouse"), "yes", "xHCI + HID are published specs"),
    (("system",), "yes", "ACPI/PCI enumeration glue, mostly standard"),
)


def read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def field(text: str, key: str) -> str:
    match = re.search(rf"^\s*{key}\s*=\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip().strip('"') if match else ""


VENDORS = {
    "1002": "AMD", "10DE": "NVIDIA", "10EC": "Realtek", "8086": "Intel",
    "1022": "AMD", "17CB": "Qualcomm", "168C": "Qualcomm/Atheros",
    "14E4": "Broadcom", "1B21": "ASMedia", "1CC1": "ADATA",
    "1B4B": "Marvell", "1969": "Atheros",
}


def vendors_in(ids: list[str]) -> set[str]:
    found = set()
    for ident in ids:
        match = re.search(r"VEN_([0-9A-Fa-f]{4})", ident)
        if match:
            found.add(match.group(1).upper())
    return found


def reference_for(device_class: str, ids: list[str]) -> str:
    """Which open implementation documents this device class?

    Linux module names are the reference: they are readable source for the
    hardware behaviour. They are GPL-2.0, so read them - do not copy code into
    a permissively licensed kernel.
    """
    cls = device_class.lower()
    vendors = vendors_in(ids)

    if "display" in cls or "monitor" in cls:
        if "10DE" in vendors:
            return "nouveau (reverse-engineered; firmware needed for reclocking)"
        if "1002" in vendors:
            return "amdgpu (open, but requires firmware blobs)"
        return "vendor GPU driver (closed)"

    if cls == "net" or "network" in cls:
        if "10EC" in vendors:
            return "r8169 (Ethernet, fully open)"
        if "8086" in vendors:
            return "e1000e / igb (Ethernet, fully open)"
        if "14E4" in vendors:
            return "tg3 (Ethernet, fully open)"
        if vendors & {"168C", "17CB"}:
            return "ath10k / ath11k (Wi-Fi, needs firmware)"
        return "check LKDDb; Wi-Fi generally needs firmware"

    if "scsiadapter" in cls or "hdc" in cls:
        return "ahci (published standard)"
    if "nvme" in cls or "diskdrive" in cls:
        return "nvme (published standard)"
    if "usb" in cls or "hid" in cls or "keyboard" in cls or "mouse" in cls:
        return "xhci_hcd + usbhid (published standards)"
    if "media" in cls or "audio" in cls:
        return "snd_hda_intel (Intel HDA spec)"
    if "bluetooth" in cls:
        return "btusb (needs firmware)"
    if "system" in cls:
        return "ACPI / pinctrl glue (pinctrl_amd, i2c_piix4)"
    return "unclassified"


def classify(device_class: str) -> tuple[str, str]:
    key = device_class.lower()
    for keywords, verdict, why in RULES:
        if any(word in key for word in keywords):
            return verdict, why
    return "maybe", "Unclassified - needs a look"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="hardware inventory from INFs")
    parser.add_argument("--root", default=r"E:\PC_Drivers")
    parser.add_argument("--out", default=r"D:\OS\hardware_inventory.csv")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"driver root not found: {args.root}")
        return 2

    rows = []
    skipped = 0
    for entry in sorted(os.listdir(args.root)):
        package = os.path.join(args.root, entry)
        if not os.path.isdir(package):
            continue
        infs = [f for f in os.listdir(package) if f.lower().endswith(".inf")]
        if not infs:
            skipped += 1
            continue
        for inf in infs:
            text = read_text(os.path.join(package, inf))
            if not text:
                skipped += 1
                continue
            device_class = field(text, "Class")
            provider = field(text, "Provider") or field(text, "ProviderName")
            ids = []
            for match in HW_ID.findall(text):
                ident = match.strip()
                if ident not in ids:
                    ids.append(ident)
            verdict, why = classify(device_class)
            rows.append({
                "package": entry,
                "inf": inf,
                "class": device_class or "(none)",
                "provider": provider,
                "vendors": ",".join(sorted(VENDORS.get(v, v) for v in vendors_in(ids))),
                "feasibility": verdict,
                "note": why,
                "open_reference": reference_for(device_class, ids),
                "hardware_ids": ";".join(ids[:8]),
                "id_count": len(ids),
            })

    if not rows:
        print("no .inf files parsed")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"parsed {len(rows)} driver package(s), skipped {skipped}")
    print(f"written: {args.out}\n")

    verdicts = Counter(row["feasibility"] for row in rows)
    print("feasibility:")
    for verdict in ("yes", "maybe", "no"):
        print(f"  {verdict:<6} {verdicts.get(verdict, 0)}")

    print("\nby class:")
    for device_class, count in Counter(row["class"] for row in rows).most_common():
        print(f"  {device_class:<18} {count}")

    print("\npackages with the most hardware IDs:")
    for row in sorted(rows, key=lambda r: -r["id_count"])[:5]:
        print(f"  {row['id_count']:3d}  {row['class']:<12} {row['inf']}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
