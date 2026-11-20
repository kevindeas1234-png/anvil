#!/bin/sh
# Run the ANVIL test on THIS machine and print a block ready to paste back.
#
#   ./report.sh          build, boot, verify, summarise
#
# It runs test.sh (or reports why it could not), then prints only the handful of
# lines worth comparing between machines. Nothing is uploaded anywhere.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LOG="$HERE/logs/report.log"

echo "running the test..."
( cd "$HERE" && ./test.sh report >/dev/null 2>&1 ) || true

if [ ! -f "$LOG" ]; then
    echo "--- COPY FROM HERE ---"
    echo "no serial log was produced - the emulator never booted"
    echo "--- TO HERE ---"
    exit 1
fi

region=$(grep -o 'store: region fnv=0x[0-9a-f]*' "$LOG" | tail -1 | cut -d= -f2)
result=$(grep -c 'store: replay identical' "$LOG" >/dev/null 2>&1 && echo PASS || echo UNKNOWN)
cpus=$( (grep -m1 'model name' /proc/cpuinfo 2>/dev/null || sysctl -n machdep.cpu.brand_string 2>/dev/null) | cut -d: -f2- | sed 's/^ *//')
osname=$(uname -srm 2>/dev/null)

echo
echo "--- COPY FROM HERE ---"
echo "machine : ${osname:-unknown}"
echo "cpu     : ${cpus:-unknown}"
have() { command -v "$1" >/dev/null 2>&1; }
pyver() {
    for c in python3 python py; do
        if have "$c" && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            "$c" -c 'import sys; print(sys.version.split()[0])'; return
        fi
    done
    echo "not found"
}
toolver() {  # label, candidate, version-args...
    label=$1; shift; cand=$1; shift
    for c in "$cand" "$HERE/tools/$cand/$cand" "$HERE/tools/$cand/$cand.exe"; do
        if [ -x "$c" ] || have "$c"; then
            v=$("$c" "$@" 2>/dev/null | head -1)
            [ -n "$v" ] && { printf '%s: %s
' "$label" "$v"; return; }
        fi
    done
    printf '%s: not found
' "$label"
}
printf 'python  : '; pyver
toolver zig zig version
toolver nasm nasm -v
toolver qemu qemu-system-x86_64 --version
echo "milestone: $(grep -o 'replay identical' "$LOG" >/dev/null 2>&1 && echo 10 || echo 'not reached')"
echo "region  : ${region:-none}"
echo "result  : ${result}"
echo "--- TO HERE ---"
echo
echo "This machine's region hash compared with 0x50370dfe is the whole test."
