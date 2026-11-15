# Security

No security review has been performed on this project. It is a hobby-scale codebase with
no security response process and no guarantee of a fix.

## Reporting

Open an issue titled `[security] ...` with a description but without a working exploit.
Expect no particular response time.

## Known exposures, by design

| Raw sector access | ANVIL has no partition table parser, no filesystem and no write protection. Sandboxing comes from `run_vm.bat` (file-backed images plus `-snapshot`), not from the kernel. |

## General

The documentation states plainly what each tool does at its edges. Where a risk is inherent
to the design rather than an oversight, it is described there rather than left to be
discovered.