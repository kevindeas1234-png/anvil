@echo off
setlocal
rem ANVIL + link demo: boot the kernel and talk to it over localhost TCP.
rem Serial goes to 127.0.0.1:4444 (a chardev, not a NIC - networking stays off).
set "OSROOT=%~dp0"

echo ==========================================================
echo   ANVIL - serial link demo
echo ==========================================================
echo.

echo [1/2] building
call "%OSROOT%build.bat"
if errorlevel 1 goto done

set "QEMU="
if exist "%OSROOT%tools\qemu\qemu-system-x86_64.exe" set "QEMU=%OSROOT%tools\qemu\qemu-system-x86_64.exe"
if not defined QEMU if exist "%ProgramFiles%\qemu\qemu-system-x86_64.exe" set "QEMU=%ProgramFiles%\qemu\qemu-system-x86_64.exe"
if not defined QEMU (
  where qemu-system-x86_64.exe >nul 2>nul
  if not errorlevel 1 set "QEMU=qemu-system-x86_64.exe"
)
if not defined QEMU (
  echo [AOS] qemu-system-x86_64 was not found.
  goto done
)

taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul

echo [2/2] booting with the serial link on 127.0.0.1:4444
start "" /b "%QEMU%" -drive format=raw,if=floppy,file="%OSROOT%images\anvil.img" -snapshot -nic none -display none -serial tcp:127.0.0.1:4444,server=on,wait=on -device isa-debug-exit,iobase=0x501,iosize=0x04 -m 256 -no-reboot
timeout /t 1 /nobreak >nul

set "PYEXE=py"
where py.exe >nul 2>nul
if errorlevel 1 set "PYEXE=python"

"%PYEXE%" "%OSROOT%tools\anvil_link.py"
set "RC=%errorlevel%"

taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul

echo.
echo [AOS] link exit code: %RC%  (0 = all commands answered)
echo [AOS] transcript: logs\link.log

:done
echo.
pause
endlocal
