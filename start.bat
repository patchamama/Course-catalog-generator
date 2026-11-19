@echo off
:: ─────────────────────────────────────────────────────────────────────────────
::  Catalog — DMS Lite  |  Start Script (Windows)
:: ─────────────────────────────────────────────────────────────────────────────
::  Opens the startup progress screen in the browser, checks all services,
::  installs Python dependencies if needed, and launches the Flask backend.
::
::  Usage:
::    start.bat                          double-click or run from Explorer
::    start.bat --no-browser             headless mode
::    start.bat --backend-port 8080      custom backend port
::    start.bat --no-install             skip pip install
:: ─────────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

:: Switch console to UTF-8 so the launcher's Unicode box-drawing chars display correctly
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

set "SCRIPT_DIR=%~dp0"
set "PYTHON="

:: Try python3 then python then py launcher
for %%C in (python3 python py) do (
  if "!PYTHON!"=="" (
    where %%C >nul 2>&1 && (
      set "PYTHON=%%C"
    )
  )
)

if "!PYTHON!"=="" (
  echo.
  echo   X  Python 3.9 or higher is required but was not found.
  echo      Install from: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

:: Verify version ^>= 3.9
for /f "tokens=*" %%V in ('!PYTHON! -c "import sys; ok=sys.version_info>=(3,9); print('ok' if ok else 'old')" 2^>nul') do set "VER_CHECK=%%V"

if not "!VER_CHECK!"=="ok" (
  echo.
  echo   X  Python 3.9 or higher is required.
  echo      Install from: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo.
for /f "tokens=*" %%V in ('!PYTHON! --version') do echo   Using %%V
echo   Launcher: %SCRIPT_DIR%tools\launcher.py
echo.

"!PYTHON!" "%SCRIPT_DIR%tools\launcher.py" %*

if errorlevel 1 (
  echo.
  echo   The launcher exited with an error.
  pause
)
