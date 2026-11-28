# M28 — the machine can hear a keyboard

**Status:** specification. Nothing implemented.
**Depends on:** the IRQ gates from 0.2.x (all sixteen vectors), the serial shell.
**Sandbox:** unchanged, plus QEMU `sendkey` for automated typing.

## The question this answers

On the B550 there is no COM port, so the shell currently exists but cannot be reached: input
comes from serial only, and metal has none. The record shows it — the shell prints `anvil>` and
then the deliberate fault fires, because nothing was ever typed.

Can a person type into ANVIL on real hardware, using nothing but what the firmware and the
keyboard controller already provide?

## Design: PS/2 first, and no trampoline

The tempting route is BIOS `INT 16h`, which needs the real-mode switch and would put input behind
the same unfinished machinery as M24. The keyboard controller is simpler and already reachable:

- **IRQ1** is already gated (all sixteen IRQ vectors are installed since the spurious-IRQ7 fix),
  and `irq_handler` already records any IRQ that is not the timer. IRQ1 currently shows up there
  as an unexpected interrupt — so the plumbing exists and is proven.
- The handler reads **port `0x60`** (scancode), translates **set 1** make/break codes to ASCII,
  and feeds a small ring buffer. Break codes (bit 7 set) are dropped except for modifier state.
- The shell reads from that ring buffer instead of polling COM1.

No BIOS, no real mode, no new dependency. And it is testable without a human at the console:
QEMU's monitor can inject keystrokes with `sendkey a`, so the whole path can be exercised by a
script in CI.

## What the kernel has to grow

- **A scancode table.** Set 1 → ASCII, unshifted and shifted. About 128 entries, `const` data.
- **A ring buffer** (64 bytes) filled from the IRQ handler and drained by the shell. Single
  producer, single consumer, no locking needed beyond disabling interrupts around the index.
- **Two input sources.** Serial and keyboard both feed the shell. The prompt must not care which
  one produced the character, and both must echo identically.
- **A record.** `evt id=input.key code= ascii=` for the first N keys, so a metal run proves what
  was received rather than what was hoped for.

## Pass condition

1. On metal, typing at the boot prompt shows characters and the shell answers.
2. In QEMU, `sendkey` in a test script produces identical characters in the log — the same
   characters, verifiable by `verify.py`.
3. IRQ1 stops appearing as an *unexpected* interrupt: it becomes the keyboard, and the record
   stream shows input arriving rather than surprise.
4. No regression: `test.bat` passes with no keyboard attached at all. Absence of a keyboard must
   never be an error.

## Order of work

1. Scancode table and translation function, unit-testable in isolation.
2. IRQ1 handler: read `0x60`, translate, push to ring; EOI already handled.
3. Shell: read from the ring buffer; serial and keyboard merged.
4. `input.key` records, plus `verify.py` registry entries.
5. QEMU `sendkey` test, then metal.

## What this deliberately is not

- **Not USB HID.** USB needs a host-controller stack (xHCI/UHCI) — thousands of lines. PS/2 via
  the 8042 controller works on every board that has CSM, which is every board that can boot us.
- **Not a line editor.** No history, no cursor keys, no editing beyond backspace.
- **Not a TTY abstraction.** One buffer, two producers, one consumer. A real line discipline is
  a later milestone and should be argued for on its own merits.

## What this proves, and does not

**Proves:** a person can use ANVIL on hardware with no serial port, and the input path is
verifiable by script rather than by photograph.

**Does not prove:** anything about USB keyboards, laptops with no PS/2, or keyboards whose
controller expects set 2. When a machine reports otherwise, that becomes a data point in the PCI
inventory and a reason — not a guess.
