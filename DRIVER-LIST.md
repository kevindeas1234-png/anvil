# Why there are no drivers here, and how to compile your own

## The anecdote

Before writing a line of ANVIL, I did the thing that seemed obviously first: I compiled a
list of every driver my own Windows machine was actually using, and put it in a folder
next to the kernel. The plan was simple — that list *is* the target. Those are the drivers
an operating system would have to provide, so they are the drivers I should start
learning about.

It was a useful afternoon, and it taught me two things.

**The first is that the list is long.** Not long like "a page of notes" — long like a
machine's entire personality. Chipset, storage controllers, USB host controllers, audio,
power management, the embedded controller, timers, a dozen entries for things I had never
once thought about, because Windows had always handled them invisibly and correctly.

**The second is that almost none of it is portable.** That list describes one motherboard,
at one firmware version, with the exact set of things I happen to have plugged into it.
There is no useful sense in which it could be shipped as "the drivers ANVIL needs", because
on your machine the majority of its entries would be wrong. It is not a specification. It
is a fingerprint.

That was the point at which the project stopped being *"write an OS that talks to my
hardware"* and became *"write a kernel that can be provably seen to work, in a sandbox,
against a device whose contents I can hash"*. virtio-blk in QEMU isn't a cop-out — it was
the only honest target available.

## So compile your own

It is the cheapest part of the whole project, and it defines the work. Windows:

```powershell
pnputil /enum-drivers
Get-PnpDevice -PresentOnly | Select-Object Status,Class,FriendlyName,InstanceId
Get-CimInstance Win32_PnPSignedDriver | Select-Object DeviceName,DriverVersion,DriverProviderName
msinfo32        # Components -> Problem Devices (often the most informative page)
```

Linux, if you want a second opinion on the same machine:

```
lspci -nnk
lsusb
lsmod
udevadm info --export-db
```

Then group by **class**, and ask which classes a kernel cannot avoid. For storage the
order is forced: the bus (PCI/PCIe), then the controller (AHCI, NVMe, USB), then the block
layer. Each of those is a project on its own — AHCI is a few thousand lines and a great
deal of waiting for interrupts that may not arrive.

This is also the honest way to judge any "AI writes an OS" claim, including this one:
enumerate the classes, and see how many the project has actually touched. ANVIL has
touched exactly one device class, in a virtual machine, and says so.

## Why the list stays out of this repository

- **It describes one machine**, so as a shared artifact it is close to worthless.
- **It contains your hardware IDs and firmware revisions** — your machine's fingerprint.
  Committing it would publish that.
- **It would imply a driver list is a plan.** It isn't. It's a survey. The plan is the
  decision about which class to attack first, and that decision is made with a pass
  condition, not a spreadsheet.

Mine still exists, in the folder I reserved for testing, and it is still useful. Just not
as code.

## The first milestone that would make it matter

If you want ANVIL to say something *true* about real hardware without pretending to drive
it, the boundary is small and worth marking: have the kernel enumerate the PCI bus itself,
print each vendor and device ID, and compare that output with your own list.

That is a claim a stranger can check against their own machine — and it is the natural
next step after the arbitrary-LBA read, because it uses exactly the device enumeration the
virtio driver already performs, with nothing assumed about what it finds.
