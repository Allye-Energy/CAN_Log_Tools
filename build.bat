@echo off
setlocal

echo ============================================
echo  CAN Log Tools  ^|  PyInstaller build
echo ============================================
echo.

REM ── Check Python is available ──────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found on PATH.
    echo Make sure Python is installed and added to your PATH.
    pause & exit /b 1
)

REM ── Check / install PyInstaller ────────────────────────────────────────────
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found -- installing...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause & exit /b 1
    )
)

REM ── Resolve paths relative to this script ──────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "APP_NAME=can_tools"
set "SCRIPT=%SCRIPT_DIR%can_tools_app.py"
set "ICON=%SCRIPT_DIR%log_to_asc.ico"

if not exist "%SCRIPT%" (
    echo ERROR: can_tools_app.py not found in %SCRIPT_DIR%
    pause & exit /b 1
)

if not exist "%ICON%" (
    echo WARNING: log_to_asc.ico not found -- building without icon.
    set "ICON_ARG="
) else (
    set "ICON_ARG=--icon="%ICON%""
)

REM ── Run PyInstaller ────────────────────────────────────────────────────────
echo Building %APP_NAME%.exe ...
echo.

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "%APP_NAME%" ^
    %ICON_ARG% ^
    --clean ^
    "%SCRIPT%"

if errorlevel 1 (
    echo.
    echo BUILD FAILED. See output above for details.
    pause & exit /b 1
)

echo.
echo ============================================
echo  Build complete!
echo  Output: %SCRIPT_DIR%dist\%APP_NAME%.exe
echo ============================================
echo.

REM ── Open the dist folder in Explorer ───────────────────────────────────────
explorer "%SCRIPT_DIR%dist"

pause
endlocal
