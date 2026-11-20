@echo off
setlocal
rem ANVIL - boot the same image as a USB mass storage device.
rem
rem This exists to answer one question: does the boot loader depend on floppy
rem geometry? The device is a file, not a stick - real firmware compatibility is
rem a different question, and a different (parked) project.
rem
rem No physical USB is required, and none is written to.
set "OSROOT=%~dp0"
set "NAME=%~1"
if "%NAME%"=="" set "NAME=usb"

set "IMAGE=%OSROOT%images\anvil.img"
if not "%~2"=="" set "IMAGE=%~2"

if not exist "%IMAGE%" (
  echo [AOS] images\anvil.img is missing - run build.bat first.
  pause
  exit /b 1
)

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
if exist "%LOG%" del /q "%LOG%"

echo [AOS] emulator: %QEMU%
echo [AOS] booting with the image attached as USB mass storage
echo [AOS] snapshot, no NIC, no display, 256 MB
echo [AOS] serial output: logs\%NAME%.log
echo.

taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul

rem bootindex=0 puts the USB stick first in the firmware's boot order, so the
rem result does not depend on how SeaBIOS happens to enumerate devices today.
start "" /b "%QEMU%" ^
 -device qemu-xhci ^
 -drive id=usbimg,if=none,format=raw,file="%IMAGE%" ^
 -device usb-storage,drive=usbimg,bootindex=0 ^
 -drive file="%OSROOT%images\testdisk.img",format=raw,if=virtio ^
 -snapshot -nic none -display none -serial "file:%LOG%" ^
 -device isa-debug-exit,iobase=0x501,iosize=0x04 -m 256 -no-reboot

set /a WAITED=0
set SEEN=0
:watch
ping -n 2 127.0.0.1 >nul 2>nul
set /a WAITED+=1
tasklist /fi "imagename eq qemu-system-x86_64.exe" | find /i "qemu-system" >nul 2>nul
if errorlevel 1 goto notseen
set SEEN=1
if %WAITED% lss 10 goto watch
taskkill /IM qemu-system-x86_64.exe /F >nul 2>nul
goto settled
:notseen
rem Not seeing QEMU yet is not the same as QEMU having finished: at start-up the
rem process may not have appeared, and treating that as "it exited" lets the batch
rem return while the guest is still writing, so the caller reads a half-written
rem serial log. Wait for it to appear before believing it has gone.
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
endlocal