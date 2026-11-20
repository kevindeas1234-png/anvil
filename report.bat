@echo off
setlocal enabledelayedexpansion
rem Run the ANVIL test on THIS machine and print a block ready to paste back.
rem   report.bat
rem Nothing is uploaded anywhere; this only reads the serial log it just produced.
set "HERE=%~dp0"
set "LOG=%HERE%logs\report.log"

echo running the test...
call "%HERE%test.bat" report >nul 2>&1

if not exist "%LOG%" (
    echo --- COPY FROM HERE ---
    echo no serial log was produced - the emulator never booted
    echo --- TO HERE ---
    exit /b 1
)

echo.
echo --- COPY FROM HERE ---
for /f "tokens=2 delims=:" %%a in ('wmic cpu get name /value ^| find "Name="') do echo cpu     : %%a
echo os      : %OS% %PROCESSOR_ARCHITECTURE%
echo zig     : 
zig version 2>nul || echo zig     : not on PATH
nasm -v 2>nul
qemu-system-x86_64 --version 2>nul | findstr /i version
echo milestone: 
findstr /c:"store: replay identical" "%LOG%" >nul && echo milestone: 10
echo region  :
for /f "tokens=2 delims==" %%a in ('findstr /c:"store: region fnv=" "%LOG%"') do echo region  : %%a
echo result  :
findstr /c:"store: replay identical" "%LOG%" >nul && echo result  : PASS
echo --- TO HERE ---
echo.
echo Compare the region hash with 0x50370dfe. That is the whole test.
