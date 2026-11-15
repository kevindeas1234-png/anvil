@echo off
setlocal
rem AOS sandboxed VM runner.
rem Snapshot mode: writes are discarded when the VM exits.
rem No network, no display, serial only. Nothing outside this folder is touched.
set "OSROOT=%~dp0"
set "NAME=%~1"
if "%NAME%"=="" set "NAME=boot"

if not exist "%OSROOT%images\anvil.img" (
  echo [AOS] images\anvil.img is missing - run build.bat first.
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
  echo [AOS] Expected: %OSROOT%tools\qemu\qemu-system-x86_64.exe
  echo [AOS]          or C:\Program Files\qemu\qemu-system-x86_64.exe
  echo [AOS]          or qemu-system-x86_64.exe on PATH.
  pause
  exit /b 1
)

if not exist "%OSROOT%logs" mkdir "%OSROOT%logs"
set "LOG=%OSROOT%logs\%NAME%.log"
if exist "%LOG%" del /q "%LOG%"

echo [AOS] emulator: %QEMU%
echo [AOS] booting sandboxed VM... snapshot, no NIC, no display, 256 MB
echo [AOS] serial output: logs\%NAME%.log
echo.

rem Kill any VM left over from an earlier run: a stuck instance keeps the log
rem file open, so the next run would append to it and confuse the results.
taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul

rem Start the guest detached, so a hung kernel cannot hold this window open.
start "" /b "%QEMU%" -drive format=raw,if=floppy,file="%OSROOT%images\anvil.img" -drive file="%OSROOT%images\testdisk.img",format=raw,if=virtio -snapshot -nic none -display none -serial "file:%LOG%" -device isa-debug-exit,iobase=0x501,iosize=0x04 -m 256 -no-reboot

rem Watchdog: give the guest up to 8 seconds, then stop it. A hung kernel can
rem no longer block the test run, and an early isa-debug-exit ends the wait.
rem
rem `timeout /t` is not used here: it refuses to run when stdin is redirected
rem ("Input redirection is not supported"), so a scheduled task or an agent run
rem would skip the wait entirely, kill the VM at once and log nothing. ping is
rem used as the clock instead, because it works with no console attached.
set /a WAITED=0
:watch
ping -n 2 127.0.0.1 >nul 2>nul
tasklist /fi "imagename eq qemu-system-x86_64.exe" | find /i "qemu-system" >nul 2>nul
if errorlevel 1 goto stopped
set /a WAITED+=1
if %WAITED% lss 8 goto watch
taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul
:stopped

echo.
echo [AOS] VM exited.
echo ----- serial log -----
type "%LOG%"
echo ----------------------
echo.
echo [AOS] full log: %LOG%
echo.
echo [AOS] next: python "%OSROOT%verify.py"
endlocal
