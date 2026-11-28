# M27 — the machine can draw on any screen

**Status:** specification. Nothing implemented.
**Depends on:** M14 (boot), the VGA text console from 0.1.2.
**Sandbox:** unchanged. `-snapshot`, file-backed images, no NIC.

## The question this answers

The kernel writes text to `0xB8000` — the VGA text buffer. That works on any machine with a
legacy VGA BIOS, and it is why the banner and the step counter appeared on the B550 at all. It
also stops at 80×25 characters, one colour, and nothing else: no graphics, no fonts we own, no
future.

Can the kernel draw into a framebuffer the firmware chose for it — without a display driver in
the kernel?

## Design: the mode is set in real mode, by the firmware

A graphics mode is not something a kernel should set itself. `INT 10h AX=4F02h` (VBE) asks the
firmware to pick a linear framebuffer mode; `AX=4F01h` reports its address, pitch, size and depth.
Both are real-mode calls, and `stage1` is already in real mode with the BIOS available.

So the mode is chosen before the kernel exists, and `boot_info` gains four fields:

| Field | Meaning |
|---|---|
| `fb_addr` | physical address of the linear framebuffer (0 = no VBE, use VGA text) |
| `fb_pitch` | bytes per scanline |
| `fb_width`, `fb_height` | pixels |
| `fb_bpp` | bits per pixel (expect 32, in RGB order) |

The kernel then owns a *linear memory buffer* and nothing else. No PCI, no GPU programming, no
driver — writing a pixel is a store to memory. That is the entire reason this milestone is
approachable.

## What the kernel has to grow

- **A font.** An 8×16 bitmap for printable ASCII, embedded as a `const` array. About 1 KB of
  image, and it is the only new asset. This is the first time the kernel carries data rather
  than code, which is worth noticing.
- **A blitter.** `fb_putc(x, y, c)` that writes 16 rows of 8 pixels through the pitch, honouring
  32 bpp and the channel order the mode reports.
- **A console that keeps working.** `vga_putc` becomes `con_putc`: framebuffer if `fb_addr` is
  set, VGA text otherwise. The banner, step counter and panic dump must render identically in
  both worlds, or the diagnostics we depend on become conditional on hardware.

## Pass condition

1. On a VBE machine, the banner, step counter and console are legible at 1024×768×32.
2. `evt id=video.mode mode=fb width= pitch= bpp=` fires, and on a machine without VBE the same
   record fires with `mode=vga` and the text console still works.
3. `test.bat` still passes: serial output unchanged, every hash identical. The screen is a
   display, not a channel the harness depends on.
4. Panic output renders on the framebuffer, and the step counter survives scrolling there as it
   does in text mode.

## Order of work

1. `stage1`: VBE mode query and set, fields into `boot_info`, and a marker if it fails.
2. Font table; blitter; `con_putc` split with the VGA path kept intact.
3. Move banner, step counter and panic rendering onto `con_putc`.
4. Record `video.mode`; extend `verify.py`'s registry.
5. Screenshot both paths in QEMU (`-vga std` gives VBE) and compare with the text-mode capture.

## What this deliberately is not

- **Not a GUI.** One font, one colour, no windowing, no mouse. The point is a framebuffer that
  works everywhere, not a desktop.
- **Not a GPU driver.** No acceleration, no modesetting after boot, no EDID.
- **Not a replacement for serial.** The harness still reads COM1; the screen remains a courtesy
  to humans.

## What this proves, and does not

**Proves:** the kernel can put pixels on any machine with a VGA BIOS without writing a single
driver, and the diagnostics survive the change of medium.

**Does not prove:** anything about output on a machine with no VBE, a UEFI-only machine, or a
display that needs a native driver. Those stay parked, and the VGA text path stays as the floor.
