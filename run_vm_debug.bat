@echo off
setlocal
rem AOS sandboxed VM runner, with QEMU's own interrupt/reset tracing.
rem Use this when the plain runner produces an empty or truncated log.
set "OSROOT=%~dp0"
set "NAME=%~1"
if "%NAME%"=="" set "NAME=debug"

if not exist "%OSROOT%images\anvil.img" (
  echo [AOS] images\anvil.img is missing - run build.bat first.
  pause
  exit /b 1
)

where qemu-system-x86_64.exe >nul 2>nul
if errorlevel 1 (
  echo [AOS] qemu-system-x86_64 was not found on PATH.
  pause
  exit /b 1
)

rem Prefer a local QEMU, then the usual install location, then PATH.
set "QEMU="
if exist "%OSROOT%tools\qemu\qemu-system-x86_64.exe" set "QEMU=%OSROOT%tools\qemu\qemu-system-x86_64.exe"
if not defined QEMU if exist "%ProgramFiles%\qemu\qemu-system-x86_64.exe" set "QEMU=%ProgramFiles%\qemu\qemu-system-x86_64.exe"
if not defined QEMU (
  where qemu-system-x86_64.exe >nul 2>nul
  if not errorlevel 1 set "QEMU=qemu-system-x86_64.exe"
)
if not defined QEMU (
  echo [AOS] qemu-system-x86_64 was not found.
  pause
  exit /b 1
)

if not exist "%OSROOT%logs" mkdir "%OSROOT%logs"
set "LOG=%OSROOT%logs\%NAME%.log"
set "TRACE=%OSROOT%logs\%NAME%_qemu.log"
if exist "%LOG%" del /q "%LOG%"
if exist "%TRACE%" del /q "%TRACE%"

echo [AOS] booting sandboxed VM with tracing...
echo [AOS] serial : logs\%NAME%.log
echo [AOS] trace  : logs\%NAME%_qemu.log
echo.

"%QEMU%" -drive format=raw,if=floppy,file="%OSROOT%images\anvil.img" -drive file="%OSROOT%images\testdisk.img",format=raw,if=virtio -snapshot -nic none -display none -serial "file:%LOG%" -device isa-debug-exit,iobase=0x501,iosize=0x04 -m 256 -no-reboot -d int,cpu_reset,guest_errors -D "%TRACE%"

echo.
echo [AOS] VM exited.
echo ----- serial log -----
type "%LOG%"
echo ----------------------
echo.
echo [AOS] guest errors / interrupts: see %TRACE%
echo [AOS] next: python "%OSROOT%verify.py" "%LOG%"
endlocal
