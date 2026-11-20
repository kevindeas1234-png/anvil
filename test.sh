#!/bin/sh
# ANVIL - build, boot, and verify, on Linux, macOS or the BSDs.
#
#   ./test.sh              build, boot headless, verify -> RESULT: PASS/FAIL
#   ./test.sh mylabel      same, but name the run (logs/mylabel.log)
#
# Tools, each optional to pre-set through the environment:
#   NASM   assembler          default: nasm, then tools/nasm/nasm
#   ZIG    C compiler/linker  default: zig,  then tools/zig/zig
#   QEMU   emulator           default: qemu-system-x86_64
#   PY     interpreter        default: python3, then python, then py
#
# Everything it writes stays in this directory: build/, images/, logs/.
# The guest runs with -snapshot, no NIC and no display, so nothing outside the
# image files is touched.
set -u

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT" || exit 1
LABEL=${1:-m1test}
LOG_REL="logs/$LABEL.log"
LOG="$ROOT/$LOG_REL"

say() { printf '[AOS] %s\n' "$1"; }
die() { printf '[AOS] %s\n' "$1" >&2; exit 1; }

# --- tools -----------------------------------------------------------------
# "On PATH" is not the same as "works": Windows ships stub launchers for python3
# that exist, run, and fail immediately. Each tool is probed with the command it
# must actually answer, and the first candidate that answers is used.
nasm_ok() { "$1" -v >/dev/null 2>&1; }
zig_ok()  { "$1" version >/dev/null 2>&1; }
qemu_ok() { "$1" --version >/dev/null 2>&1; }
py_ok()   { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

pick() {  # probe-function, then candidates in order of preference
    probe_fn=$1; shift
    for candidate in "$@"; do
        [ -n "$candidate" ] || continue
        if [ -x "$candidate" ] && "$probe_fn" "$candidate"; then
            printf '%s\n' "$candidate"; return 0
        fi
        if command -v "$candidate" >/dev/null 2>&1; then
            found=$(command -v "$candidate")
            if "$probe_fn" "$found"; then printf '%s\n' "$found"; return 0; fi
        fi
    done
    return 1
}

NASM=$(pick nasm_ok "${NASM:-}" nasm tools/nasm/nasm tools/nasm/nasm.exe) \
    || die "nasm not found or not working. Install it, or set NASM=/path/to/nasm"
ZIG=$(pick zig_ok "${ZIG:-}" zig tools/zig/zig tools/zig/zig.exe) \
    || die "zig not found or not working (0.13.0 required). Set ZIG=/path/to/zig"
QEMU=$(pick qemu_ok "${QEMU:-}" qemu-system-x86_64) \
    || die "qemu-system-x86_64 not found or not working. Set QEMU=/path/to/qemu"
PY=$(pick py_ok "${PY:-}" python3 python py) \
    || die "python 3.10+ not found (a Windows Store stub does not count). Set PY=/path/to/python"

say "root : $ROOT"
say "nasm : $NASM"
say "zig  : $ZIG"
say "qemu : $QEMU"
say "py   : $PY"
"$ZIG" version | sed 's/^/       zig version /'
"$PY" -c 'import sys; print("       python", sys.version.split()[0])'

# --- build -----------------------------------------------------------------
CCFLAGS="-target x86-freestanding -ffreestanding -nostdlib -fno-builtin -fno-pic \
-fno-stack-protector -mgeneral-regs-only -mno-mmx -mno-sse -mno-sse2 -mno-avx \
-msoft-float -O2 -c"

mkdir -p build images logs
rm -f "$LOG"

say "1/6 boot sector"
"$NASM" -f bin kernel/stage1.asm -o build/boot.bin || die "stage1 failed"
say "2/6 kernel entry"
"$NASM" -f elf32 kernel/entry.asm -o build/entry.o || die "entry failed"
say "3/6 interrupt stubs"
"$NASM" -f elf32 kernel/isr.asm -o build/isr.o || die "isr failed"
say "4/6 compiling C"
"$ZIG" cc $CCFLAGS kernel/kernel.c -o build/kernel.o || die "kernel.c failed"
"$ZIG" cc $CCFLAGS kernel/virtio.c -o build/virtio.o || die "virtio.c failed"
"$ZIG" cc $CCFLAGS kernel/store.c -o build/store.o || die "store.c failed"
say "5/6 linking at 0x10000"
"$ZIG" cc -target x86-freestanding -nostdlib -Wl,-T,"$ROOT/kernel/linker.ld" \
    -o build/kernel.elf build/entry.o build/isr.o build/kernel.o \
    build/virtio.o build/store.o || die "link failed"
say "6/6 flattening and packaging"
"$ZIG" objcopy -O binary build/kernel.elf build/kernel.bin || die "objcopy failed"
"$PY" tools/mkdisk.py images/testdisk.img >/dev/null || die "mkdisk failed"
"$PY" tools/mkimage.py build/boot.bin build/kernel.bin images/anvil.img \
    || die "mkimage failed (kernel too large?)"
say "built images/anvil.img"

# --- boot ------------------------------------------------------------------
# Relative paths inside the QEMU arguments on purpose: a POSIX path after a
# "file:" prefix is not translated for a native Windows binary, and relative
# paths are correct on every platform.
say "booting (snapshot, no NIC, no display, 256 MB)"
set -- -drive format=raw,if=floppy,file=images/anvil.img \
       -drive file=images/testdisk.img,format=raw,if=virtio \
       -snapshot -nic none -display none -serial "file:$LOG_REL" \
       -device isa-debug-exit,iobase=0x501,iosize=0x04 -m 256 -no-reboot

if command -v timeout >/dev/null 2>&1; then
    timeout 25 "$QEMU" "$@" >/dev/null 2>&1 || true
else
    "$QEMU" "$@" >/dev/null 2>&1 &
    QPID=$!
    WAITED=0
    while kill -0 "$QPID" 2>/dev/null && [ "$WAITED" -lt 25 ]; do
        sleep 1
        WAITED=$((WAITED + 1))
    done
    kill -9 "$QPID" 2>/dev/null || true
    wait "$QPID" 2>/dev/null || true
fi

if [ ! -f "$LOG" ]; then
    die "the emulator produced no serial log - is QEMU able to start?"
fi

# the serial file can still be settling when the emulator goes away
PREV=-1
for _ in 1 2 3 4 5 6 7 8 9 10; do
    NOW=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
    [ "${NOW:-0}" = "$PREV" ] && break
    PREV=${NOW:-0}
    sleep 0.3
done

# --- verify ----------------------------------------------------------------
say "verifying $LOG_REL"
CR=$(printf '\r')
# Not `python ... | tr`: the pipeline's status is tr's, which is always 0, so a
# failing verification would report success. Capture python's own status.
"$PY" verify.py "$LOG" > build/verify.out 2>&1
RC=$?
tr -d "$CR" < build/verify.out

if [ "$RC" -ne 0 ]; then
    say "RESULT: FAIL - the log above says why"
    exit 1
fi
say "RESULT: PASS"
