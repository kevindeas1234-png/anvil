#!/usr/bin/env python3
"""Numbered, hash-verified snapshots of the ANVIL source tree.

ANVIL has no version control: edits are in place, and a bad change is only
recoverable if someone remembers what it looked like. This is the aos-audit
pattern applied to it - snapshot, list, diff, restore, and a restore that is
itself reversible.

    py tools\\history.py --snapshot --reason "before virtio write path"
    py tools\\history.py --list
    py tools\\history.py --diff 3
    py tools\\history.py --restore 3

Snapshots live in <root>\\history\\run_NNNN_<timestamp>\\ and exclude build
output, images and the history folder itself. The log lives outside the
project, so archiving the source cannot destroy the record of it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

ROOT = os.environ.get("ANVIL_ROOT", r"D:\OS")
HISTORY = os.path.join(ROOT, "history")
LOG_DIR = os.environ.get("ANVIL_LOG_DIR", r"D:\ANVIL_Logs")
LOG_FILE = os.path.join(LOG_DIR, "history.log")

# directories never snapshotted (build products, binaries, the archive itself)
EXCLUDED_DIRS = {"build", "images", "logs", "history", "__pycache__",
                 os.path.join("tools", "nasm"), os.path.join("tools", "zig")}
TRACKED_EXT = (".c", ".h", ".asm", ".ld", ".py", ".bat")
EXCLUDED_FILES = {"zig.zip"}


def log(event: str, detail: str = "") -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {event:<12} | {detail}\n")


def tracked_files(root: str) -> list[str]:
    """Relative paths of source files, in a stable order."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        relative_dir = os.path.relpath(dirpath, root)
        dirnames[:] = sorted(d for d in dirnames
                             if os.path.normpath(os.path.join(relative_dir, d)) not in EXCLUDED_DIRS
                             and d not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDED_FILES or not name.endswith(TRACKED_EXT):
                continue
            found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(found)


def digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def next_run_id() -> int:
    os.makedirs(HISTORY, exist_ok=True)
    numbers = []
    for entry in os.listdir(HISTORY):
        if entry.startswith("run_"):
            try:
                numbers.append(int(entry.split("_")[1]))
            except (IndexError, ValueError):
                pass
    return max(numbers) + 1 if numbers else 1


def run_dir(run: int) -> str | None:
    for entry in sorted(os.listdir(HISTORY)) if os.path.isdir(HISTORY) else []:
        if entry.startswith(f"run_{run:04d}_"):
            return os.path.join(HISTORY, entry)
    return None


def snapshot(reason: str = "manual") -> int:
    run = next_run_id()
    target = os.path.join(HISTORY, f"run_{run:04d}_{time.strftime('%Y-%m-%d_%H%M%S')}")
    files = tracked_files(ROOT)
    entries = []

    for relative in files:
        source = os.path.join(ROOT, relative)
        destination = os.path.join(target, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        entries.append({"path": relative, "bytes": os.path.getsize(source),
                        "sha256": digest(source)})

    index = {"run": run, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
             "reason": reason, "root": ROOT, "file_count": len(entries),
             "files": entries}
    with open(os.path.join(target, "index.json"), "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2)

    catalogue_path = os.path.join(HISTORY, "runs.json")
    catalogue = []
    if os.path.isfile(catalogue_path):
        try:
            with open(catalogue_path, encoding="utf-8") as handle:
                catalogue = json.load(handle)
        except (OSError, ValueError):
            catalogue = []
    catalogue.append({"run": run, "started": index["started"], "reason": reason,
                      "files": len(entries), "dir": os.path.basename(target)})
    with open(catalogue_path, "w", encoding="utf-8") as handle:
        json.dump(catalogue, handle, indent=2)

    log("snapshot", f"run {run:04d} ({len(entries)} files) {reason}")
    print(f"[history] run {run:04d}  {len(entries)} file(s)  {target}")
    return run


def list_runs() -> int:
    catalogue_path = os.path.join(HISTORY, "runs.json")
    if not os.path.isfile(catalogue_path):
        print("no snapshots yet - run --snapshot first")
        return 0
    with open(catalogue_path, encoding="utf-8") as handle:
        catalogue = json.load(handle)
    print(f"{'RUN':>4}  {'STARTED':<19}  {'FILES':>5}  REASON")
    for entry in catalogue:
        print(f"{entry['run']:>4}  {entry['started']:<19}  {entry['files']:>5}  "
              f"{entry.get('reason', '')}")
    return 0


def diff_run(run: int) -> int:
    directory = run_dir(run)
    if not directory:
        print(f"run {run:04d} not found")
        return 2
    with open(os.path.join(directory, "index.json"), encoding="utf-8") as handle:
        index = json.load(handle)
    archived = {item["path"]: item["sha256"] for item in index["files"]}
    current = {relative: digest(os.path.join(ROOT, relative))
               for relative in tracked_files(ROOT)}

    added = sorted(set(current) - set(archived))
    removed = sorted(set(archived) - set(current))
    changed = sorted(path for path in set(archived) & set(current)
                     if archived[path] != current[path])

    print(f"run {run:04d} vs current tree")
    for label, paths in (("added", added), ("changed", changed), ("removed", removed)):
        if not paths:
            continue
        print(f"  {label}:")
        for path in paths:
            print(f"    {path}")
    if not (added or removed or changed):
        print("  identical")
    return 0


def restore(run: int) -> int:
    directory = run_dir(run)
    if not directory:
        print(f"run {run:04d} not found")
        return 2
    with open(os.path.join(directory, "index.json"), encoding="utf-8") as handle:
        index = json.load(handle)
    wanted = {item["path"] for item in index["files"]}

    # 1. capture the present first, so this restore can be undone
    undo = snapshot(reason=f"pre-restore of run {run:04d}")

    # 2. drop files the snapshot didn't have
    dropped = 0
    for relative in tracked_files(ROOT):
        if relative not in wanted:
            os.remove(os.path.join(ROOT, relative))
            log("restore", f"dropped {relative}")
            dropped += 1

    # 3. reinstate the snapshot
    restored = 0
    for relative in sorted(wanted):
        source = os.path.join(directory, relative)
        destination = os.path.join(ROOT, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        restored += 1

    log("restore", f"run {run:04d} -> {restored} restored, {dropped} dropped; undo run {undo:04d}")
    print(f"[history] restored {restored} file(s), dropped {dropped}")
    print(f"[history] undo with:  --restore {undo:04d}")
    return 0


def prune(keep: int) -> int:
    catalogue_path = os.path.join(HISTORY, "runs.json")
    if not os.path.isfile(catalogue_path):
        return 0
    with open(catalogue_path, encoding="utf-8") as handle:
        catalogue = json.load(handle)
    doomed = catalogue[:-keep] if keep > 0 and len(catalogue) > keep else []
    for entry in doomed:
        shutil.rmtree(os.path.join(HISTORY, entry["dir"]), ignore_errors=True)
        log("prune", f"run {entry['run']:04d}")
        print(f"[history] pruned run {entry['run']:04d}")
    catalogue = catalogue[len(doomed):]
    with open(catalogue_path, "w", encoding="utf-8") as handle:
        json.dump(catalogue, handle, indent=2)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="numbered snapshots of the ANVIL tree")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--reason", default="manual")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--diff", type=int, metavar="RUN")
    parser.add_argument("--restore", type=int, metavar="RUN")
    parser.add_argument("--prune", type=int, metavar="N")
    args = parser.parse_args(argv)

    if not os.path.isdir(ROOT):
        print(f"root not found: {ROOT}")
        return 2

    if args.list:
        return list_runs()
    if args.diff is not None:
        return diff_run(args.diff)
    if args.restore is not None:
        return restore(args.restore)
    if args.prune is not None:
        return prune(args.prune)
    if args.snapshot:
        snapshot(args.reason)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
