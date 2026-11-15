@echo off
setlocal
rem One-click milestone test: build, boot in the sandbox, verify the log.
rem Double-click this file. It pauses at the end so you can read or copy the result.
set "OSROOT=%~dp0"

echo ==========================================================
echo   ANVIL - kernel test
echo ==========================================================
echo.

echo [0/3] snapshotting the source tree
set "PYEXE=py"
where py.exe >nul 2>nul
if errorlevel 1 set "PYEXE=python"
"%PYEXE%" "%OSROOT%tools\history.py" --snapshot --reason "pre-test"

echo.
echo [1/3] building stage1.asm + kernel.c
call "%OSROOT%build.bat"
echo [AOS] build returned %errorlevel%
if errorlevel 1 goto failed

echo.
echo [2/3] booting the sandboxed VM
call "%OSROOT%run_vm.bat" m1test
echo [AOS] run returned %errorlevel%

echo.
echo [3/3] checking the serial log

rem Prefer the py launcher: python.exe on PATH can be a Microsoft Store
rem placeholder that exists but refuses to run.
set "PYEXE="
where py.exe >nul 2>nul
if not errorlevel 1 set "PYEXE=py"
if not defined PYEXE (
  where python.exe >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  echo [AOS] No Python launcher found - open logs\m1test.log yourself.
  goto done
)

"%PYEXE%" "%OSROOT%verify.py" "%OSROOT%logs\m1test.log"
if errorlevel 1 goto failed

echo.
echo ==========================================================
echo   RESULT: PASS
echo ==========================================================
goto done

:failed
echo.
echo ==========================================================
echo   RESULT: FAIL
echo   Copy everything above this line and send it to me.
echo ==========================================================

:done
echo.
pause
endlocal
