@echo off
setlocal
rem ANVIL kernel build: boot sector in NASM, kernel in C.
rem Flat control flow on purpose - no parenthesised blocks, no PowerShell.
rem Every step echoes a checkpoint and its exit code, so a failure is never silent.
set "OSROOT=%~dp0"

echo [AOS] root: %OSROOT%

if not exist "%OSROOT%build"  mkdir "%OSROOT%build"
if not exist "%OSROOT%images" mkdir "%OSROOT%images"

rem ---- assembler -------------------------------------------------------
set "NASM_EXE=%OSROOT%tools\nasm\nasm.exe"
if exist "%NASM_EXE%" goto have_nasm
where nasm.exe >nul 2>nul
if errorlevel 1 goto no_nasm
set "NASM_EXE=nasm.exe"
:have_nasm

rem ---- compiler --------------------------------------------------------
set "ZIG=%OSROOT%tools\zig\zig.exe"
if not exist "%ZIG%" goto no_zig

echo [AOS] nasm: %NASM_EXE%
"%NASM_EXE%" -v
echo [AOS] nasm version exit=%errorlevel%
echo [AOS] zig : %ZIG%
"%ZIG%" version
echo [AOS] zig version exit=%errorlevel%
echo.

echo [AOS] 1/6 boot sector
"%NASM_EXE%" -f bin "%OSROOT%kernel\stage1.asm" -o "%OSROOT%build\boot.bin"
echo [AOS] stage1 exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] 2/6 kernel entry
"%NASM_EXE%" -f elf32 "%OSROOT%kernel\entry.asm" -o "%OSROOT%build\entry.o"
echo [AOS] entry exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] 3/6 interrupt stubs
"%NASM_EXE%" -f elf32 "%OSROOT%kernel\isr.asm" -o "%OSROOT%build\isr.o"
echo [AOS] isr exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] 4/6 compiling kernel.c
rem -mgeneral-regs-only keeps clang to integer registers: without it, it
rem vectorises loops into SSE (xorps/movaps), which faults before the IDT
rem exists and resets the machine.
"%ZIG%" cc -target x86-freestanding -ffreestanding -nostdlib -fno-builtin -fno-pic -fno-stack-protector -mgeneral-regs-only -mno-mmx -mno-sse -mno-sse2 -mno-avx -msoft-float -O2 -c "%OSROOT%kernel\kernel.c" -o "%OSROOT%build\kernel.o"
echo [AOS] compile exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] 4b/6 compiling virtio.c
"%ZIG%" cc -target x86-freestanding -ffreestanding -nostdlib -fno-builtin -fno-pic -fno-stack-protector -mgeneral-regs-only -mno-mmx -mno-sse -mno-sse2 -mno-avx -msoft-float -O2 -c "%OSROOT%kernel\virtio.c" -o "%OSROOT%build\virtio.o"
echo [AOS] 4c/6 compiling store.c
"%ZIG%" cc -target x86-freestanding -ffreestanding -nostdlib -fno-builtin -fno-pic -fno-stack-protector -mgeneral-regs-only -mno-mmx -mno-sse -mno-sse2 -mno-avx -msoft-float -O2 -c "%OSROOT%kernel\store.c" -o "%OSROOT%build\store.o"
if errorlevel 1 goto failed
echo [AOS] compile store exit=0
echo [AOS] compile virtio exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] 5/6 linking ELF at 0x10000
"%ZIG%" cc -target x86-freestanding -nostdlib -Wl,-T,"%OSROOT%kernel\linker.ld" -o "%OSROOT%build\kernel.elf" "%OSROOT%build\entry.o" "%OSROOT%build\isr.o" "%OSROOT%build\kernel.o" "%OSROOT%build\virtio.o" "%OSROOT%build\store.o"
echo [AOS] link exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] 6/6 flattening to raw binary
"%ZIG%" objcopy -O binary "%OSROOT%build\kernel.elf" "%OSROOT%build\kernel.bin"
echo [AOS] objcopy exit=%errorlevel%
if errorlevel 1 goto fail

echo [AOS] packaging image
set "PYEXE=py"
where py.exe >nul 2>nul
if not errorlevel 1 goto have_py
set "PYEXE=python"
:have_py
"%PYEXE%" "%OSROOT%tools\mkimage.py" "%OSROOT%build\boot.bin" "%OSROOT%build\kernel.bin" "%OSROOT%images\anvil.img"
echo [AOS] mkimage exit=%errorlevel%
if errorlevel 1 goto fail

echo.
echo [AOS] preparing test disk
"%PYEXE%" "%OSROOT%tools\mkdisk.py" "%OSROOT%images\testdisk.img"
if errorlevel 1 goto fail

echo [AOS] built images\anvil.img
exit /b 0

:no_nasm
echo [AOS] NASM missing: expected %OSROOT%tools\nasm\nasm.exe
pause
exit /b 1

:no_zig
echo [AOS] Zig missing: expected %ZIG%
echo [AOS] The portable compiler should be unpacked at tools\zig
pause
exit /b 1

:fail
echo.
echo [AOS] BUILD FAILED - see the checkpoint above
pause
exit /b 1
