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

rem Watchdog: a hung kernel must not block the test run, so there is a cap - but
rem it is 45 seconds, not 8. The guest exits by itself in about five on an idle
rem host, so the cap only matters for the hung case. At 8 seconds a loaded host
rem hit the cap before the kernel printed its panic block, the VM was killed
rem mid-test, and the caller read a truncated log and reported a kernel failure.
rem A harness bug that looks exactly like a kernel bug.
rem
rem `timeout /t` is not used here: it refuses to run when stdin is redirected
rem ("Input redirection is not supported"), so a scheduled task or an agent run
rem would skip the wait entirely, kill the VM at once and log nothing. ping is
rem used as the clock instead, because it works with no console attached.
set /a WAITED=0
set SEEN=0
:watch
ping -n 2 127.0.0.1 >nul 2>nul
set /a WAITED+=1
tasklist /fi "imagename eq qemu-system-x86_64.exe" | find /i "qemu-system" >nul 2>nul
if errorlevel 1 goto notseen
set SEEN=1
if %WAITED% lss 45 goto watch
taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul
goto settled
:notseen
rem Not seeing QEMU yet is not the same as QEMU having finished: during start-up
rem the process may not have appeared, and treating that as "it exited" lets the
rem batch return while the guest is still writing - so the caller reads a
rem half-written serial log. Wait for it to appear before believing it has gone.
if %SEEN%==0 if %WAITED% lss 3 goto watch
:settled
ping -n 2 127.0.0.1 >nul 2>nul
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
