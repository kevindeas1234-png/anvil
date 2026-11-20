@echo off
setlocal
rem Run the ANVIL test on THIS machine and print a block ready to paste back.
rem
rem   report.bat
rem
rem If the test fails, the output that says WHY is printed instead of a summary.
rem The first version piped everything to nul and reported only "no serial log",
rem which hid the actual reason (a missing NASM) and cost a debugging round trip
rem on the second machine.
set "HERE=%~dp0"
set "LOG=%HERE%logs\report.log"
set "RUNLOG=%HERE%reports\report-run.txt"
if not exist "%HERE%reports" mkdir "%HERE%reports"

echo running the test...
call "%HERE%test.bat" report > "%RUNLOG%" 2>&1

if not exist "%LOG%" goto noserial
findstr /c:"store: replay identical" "%LOG%" >nul 2>&1 || goto incomplete

echo.
echo --- COPY FROM HERE ---
for /f "tokens=2 delims=:" %%a in ('wmic cpu get name /value 2^>nul ^| find "Name="') do echo cpu       : %%a
echo os        : %OS% %PROCESSOR_ARCHITECTURE%
call :showtool zig  "zig version"
call :showtool nasm "nasm -v"
call :showtool qemu "qemu-system-x86_64 --version"
echo milestone : 10
for /f "tokens=2 delims==" %%a in ('findstr /c:"store: region fnv=" "%LOG%"') do echo region    : %%a
echo result    : PASS
echo --- TO HERE ---
echo.
echo Compare the region hash with 0x50370dfe. That is the whole test.
goto :eof

:noserial
echo.
echo --- COPY FROM HERE ---
echo The test produced no serial log. What it said:
echo.
type "%RUNLOG%"
echo --- TO HERE ---
exit /b 1

:incomplete
echo.
echo --- COPY FROM HERE ---
echo The test ran but produced no completed run. Last 20 log lines:
echo.
powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20" 2>nul
if errorlevel 1 type "%LOG%"
echo --- TO HERE ---
exit /b 1

:showtool
rem label, command - prints the version, or says it was not found on PATH.
rem A tool can be missing from PATH and still be used from tools\, so this is
rem information rather than a verdict.
setlocal
set "LABEL=%~1"
set "CMD=%~2"
for /f "delims=" %%v in ('%CMD% 2^>nul') do (
    echo %LABEL%    : %%v
    endlocal
    goto :eof
)
echo %LABEL%    : not on PATH - a bundled copy in tools\ may still be used
endlocal
goto :eof
