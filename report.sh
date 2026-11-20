#!/bin/sh
# Run the ANVIL test on THIS machine and print a block ready to paste back.
#
#   ./report.sh          build, boot, verify, summarise
#
# If the test fails, the output that says WHY is printed instead of a summary.
# The first version discarded everything and reported only "no serial log",
# which hid the real reason and cost a debugging round trip on a second machine.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LOG="$HERE/logs/report.log"
RUNLOG="$HERE/reports/report-run.txt"
mkdir -p "$HERE/reports"

echo "running the test..."
( cd "$HERE" && ./test.sh report ) > "$RUNLOG" 2>&1 || true

if [ ! -f "$LOG" ]; then
    echo
    echo "--- COPY FROM HERE ---"
    echo "The test produced no serial log. What it said:"
    echo
    cat "$RUNLOG"
    echo "--- TO HERE ---"
    exit 1
fi

if ! grep -q 'store: replay identical' "$LOG"; then
    echo
    echo "--- COPY FROM HERE ---"
    echo "The test ran but produced no completed run. Last 20 log lines:"
    echo
    tail -20 "$LOG"
    echo "--- TO HERE ---"
    exit 1
fi

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
            [ -n "$v" ] && { printf '%s: %s\n' "$label" "$v"; return; }
        fi
    done
    printf '%s: not found on PATH - a bundled copy in tools/ may still be used\n' "$label"
}

region=$(grep -o 'store: region fnv=0x[0-9a-f]*' "$LOG" | tail -1 | cut -d= -f2)

echo
echo "--- COPY FROM HERE ---"
echo "machine   : $(uname -srm 2>/dev/null || echo unknown)"
echo "cpu       : $((grep -m1 'model name' /proc/cpuinfo 2>/dev/null || sysctl -n machdep.cpu.brand_string 2>/dev/null) | cut -d: -f2- | sed 's/^ *//')"
printf 'python    : '; pyver
toolver zig zig version
toolver nasm nasm -v
toolver qemu qemu-system-x86_64 --version
echo "milestone : 10"
echo "region    : ${region:-none}"
echo "result    : PASS"
echo "--- TO HERE ---"
echo
echo "Compare the region hash with 0x50370dfe. That is the whole test."
