@echo off
:: -----------------------------------------------------------------------------
::  Catalog - DMS Lite  |  Start Script (Windows)
:: -----------------------------------------------------------------------------
::  One command that gets everything running:
::    1. makes sure Python 3.9+ is installed (winget, no administrator needed)
::    2. the launcher then installs the Python packages (.venv), the system requirements
::       - Docker Desktop, Ollama, Node.js, pandoc, ffmpeg - starts Elasticsearch (Docker) and
::       Ollama, launches the backend and opens the app in your browser.
::
::  Missing software is installed with winget (Windows 10/11 "App Installer"). Windows may show an
::  administrator prompt for Docker Desktop, and Docker Desktop may ask for a restart / WSL 2 the first time.
::
::  Usage:
::    start.bat                          double-click or run from Explorer
::    start.bat --no-browser             headless mode
::    start.bat --no-system-install      do NOT install Docker/Ollama/Node/...; start what is installed
::    start.bat --with-extras            also VLC (video buttons) and Tesseract (OCR)
::    start.bat --no-install             skip pip install of the Python packages
::    start.bat --backend-port 8080      custom backend port
:: -----------------------------------------------------------------------------
setlocal enabledelayedexpansion

:: Switch console to UTF-8 so the launcher's Unicode characters display correctly
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

set "SCRIPT_DIR=%~dp0"
set "PYTHON="
set "SYSTEM_INSTALL=1"
for %%A in (%*) do (
  if /I "%%~A"=="--no-system-install" set "SYSTEM_INSTALL=0"
)

call :find_python
if "!PYTHON!"=="" if "!SYSTEM_INSTALL!"=="1" call :install_python

if "!PYTHON!"=="" (
  echo.
  echo   X  Python 3.9 or higher is required but was not found.
  echo      Install it from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
  echo      or run:  winget install -e --id Python.Python.3.12
  echo.
  pause
  exit /b 1
)

echo.
for /f "tokens=*" %%V in ('"!PYTHON!" --version') do echo   Using %%V
echo   Launcher: %SCRIPT_DIR%tools\launcher.py
echo.

"!PYTHON!" "%SCRIPT_DIR%tools\launcher.py" %*

if errorlevel 1 (
  echo.
  echo   The launcher exited with an error.
  pause
)
exit /b 0


:: ---------------------------------------------------------------------------
:: Sets PYTHON to a working Python 3.9+ (ignores the Microsoft Store "python" stub that opens the Store)
:: ---------------------------------------------------------------------------
:find_python
set "PYTHON="
for %%C in (python3 python py) do (
  if "!PYTHON!"=="" (
    where %%C >nul 2>&1 && (
      %%C -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PYTHON=%%C"
    )
  )
)
:: not on PATH yet (e.g. just installed by winget): look in the per-user install folder
if "!PYTHON!"=="" (
  for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" if "!PYTHON!"=="" (
      "%%D\python.exe" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1 && set "PYTHON=%%D\python.exe"
    )
  )
)
exit /b 0


:: ---------------------------------------------------------------------------
:: Installs Python 3.12 for the current user with winget
:: ---------------------------------------------------------------------------
:install_python
echo.
echo   Python 3.9+ was not found - installing Python 3.12 with winget ...
where winget >nul 2>&1
if errorlevel 1 (
  echo   X  winget is not available on this PC ^(it ships with Windows 10/11 as "App Installer"^).
  exit /b 1
)
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :find_python
exit /b 0
