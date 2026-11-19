@echo off
:: Thin wrapper — delegates all logic to setup-dev.py (single source of truth).
:: setup-dev.py handles venv creation, Docker, Elasticsearch, indexing, Flask, and Ollama.
::
:: Usage:
::   setup-dev.bat                                       (interactive)
::   setup-dev.bat C:\path\to\courses                    (incremental index)
::   setup-dev.bat --reset C:\path\to\courses            (wipe + full reindex)
::   setup-dev.bat --reset                               (interactive with forced reset)
::   setup-dev.bat --ext pdf,md C:\path\to\courses       (index only pdf and md files)
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

"!PYTHON!" "!SCRIPT_DIR!\setup-dev.py" %*
endlocal
