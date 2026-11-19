@echo off
:: Thin wrapper — delegates all logic to start.py (single source of truth).
:: start.py handles venv creation, catalog generation, Ollama check, and Flask server.
::
:: Usage:
::   start.bat                           (interactive, shows env.py default)
::   start.bat "C:\Users\Me\Courses"     (use given path directly)
::   start.bat --help
setlocal enabledelayedexpansion

SET "SCRIPT_DIR=%~dp0"
IF "!SCRIPT_DIR:~-1!"=="\" SET "SCRIPT_DIR=!SCRIPT_DIR:~0,-1!"

SET "PYTHON="
FOR %%C IN (python python3 py) DO (
    IF NOT DEFINED PYTHON (
        %%C --version >nul 2>&1 && SET "PYTHON=%%C"
    )
)
IF NOT DEFINED PYTHON (
    echo Python 3.9+ is required. Install from: https://www.python.org/downloads/
    pause & EXIT /B 1
)

"!PYTHON!" "!SCRIPT_DIR!\start.py" %*
endlocal
