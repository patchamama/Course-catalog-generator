@echo off
:: =============================================================================
::  Course Catalog — Docker Setup ^& Launch  (Windows)
:: =============================================================================
::  Starts the full Docker stack:
::    * Flask backend (catalog_server.py)  on port 5000
::    * Elasticsearch 8.x                  on port 9200
::    * (Optional) Kibana                  on port 5601
::
::  Optionally indexes your course files into Elasticsearch for fast,
::  language-aware, and semantic search via /api/es-search.
::
::  Usage:
::    start-docker.bat                             (interactive, prompts for settings)
::    start-docker.bat "C:\Users\Me\Courses"       (set courses path directly)
::    start-docker.bat "C:\Users\Me\Courses" --kibana
::    start-docker.bat --no-index
::    start-docker.bat --help
:: =============================================================================
setlocal enabledelayedexpansion

SET "SCRIPT_DIR=%~dp0"
IF "%SCRIPT_DIR:~-1%"=="\" SET "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

SET "ENV_FILE=%SCRIPT_DIR%\.env"
SET "COURSES_PATH="
SET "WITH_KIBANA=false"
SET "DO_INDEX=true"
SET "DO_GENERATE=true"
SET "COMPOSE_CMD=docker compose"

:: ── Parse arguments ──────────────────────────────────────────────────────────
FOR %%A IN (%*) DO (
    IF /I "%%~A"=="--help"        GOTO :SHOW_HELP
    IF /I "%%~A"=="-h"            GOTO :SHOW_HELP
    IF /I "%%~A"=="--kibana"      SET "WITH_KIBANA=true"
    IF /I "%%~A"=="--no-index"    SET "DO_INDEX=false"
    IF /I "%%~A"=="--no-generate" SET "DO_GENERATE=false"
)
:: First non-flag argument is the courses path
FOR %%A IN (%*) DO (
    SET "ARG=%%~A"
    IF NOT "!ARG:~0,2!"=="--" IF "%COURSES_PATH%"=="" SET "COURSES_PATH=%%~A"
)
GOTO :START

:SHOW_HELP
echo Usage: start-docker.bat [courses_path] [--kibana] [--no-index] [--no-generate]
echo.
echo   courses_path    Path to your courses directory.
echo                   If omitted, read from .env or prompted.
echo   --kibana        Also start Kibana (ES management UI at :5601).
echo   --no-index      Skip Elasticsearch indexing.
echo   --no-generate   Skip catalog HTML generation.
echo.
echo Examples:
echo   start-docker.bat
echo   start-docker.bat "C:\Users\Me\Videos\ELO Courses"
echo   start-docker.bat "C:\Users\Me\Courses" --kibana
GOTO :EOF

:START
:: ── Banner ───────────────────────────────────────────────────────────────────
echo.
echo   ====================================================
echo    Course Catalog -- Docker Setup
echo    Flask + Elasticsearch + Semantic Search
echo   ====================================================
echo.

:: =============================================================================
:: STEP 1 — Check Docker
:: =============================================================================
echo [Step 1/6] Checking Docker...

docker --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo   ERROR: Docker is not installed or not in PATH.
    echo   Install Docker Desktop from: https://www.docker.com/products/docker-desktop/
    pause & EXIT /B 1
)
FOR /F "tokens=*" %%V IN ('docker --version 2^>^&1') DO echo   OK  %%V

docker info >nul 2>&1
IF ERRORLEVEL 1 (
    echo   ERROR: Docker daemon is not running.
    echo   Please start Docker Desktop and try again.
    pause & EXIT /B 1
)

:: Check compose: prefer 'docker compose' v2, fall back to 'docker-compose' v1
docker compose version >nul 2>&1
IF ERRORLEVEL 1 (
    docker-compose --version >nul 2>&1
    IF ERRORLEVEL 1 (
        echo   ERROR: docker-compose not found.
        echo   Install Docker Desktop or see: https://docs.docker.com/compose/install/
        pause & EXIT /B 1
    )
    SET "COMPOSE_CMD=docker-compose"
)
FOR /F "tokens=*" %%V IN ('!COMPOSE_CMD! version 2^>^&1') DO (
    echo   OK  %%V
    GOTO :COMPOSE_OK
)
:COMPOSE_OK

:: =============================================================================
:: STEP 2 — Configure courses path and .env
:: =============================================================================
echo.
echo [Step 2/6] Configuration (.env)...

IF "%COURSES_PATH%"=="" (
    IF EXIST "%ENV_FILE%" (
        FOR /F "usebackq tokens=1,* delims==" %%K IN ("%ENV_FILE%") DO (
            IF "%%K"=="COURSES_PATH" SET "COURSES_PATH=%%L"
        )
    )
    IF NOT "%COURSES_PATH%"=="" (
        echo   Found in .env: COURSES_PATH=!COURSES_PATH!
        SET /P KEEP="  Keep this path? [Y/n]: "
        IF /I "!KEEP!"=="n" (
            SET "COURSES_PATH="
            SET /P COURSES_PATH="  Enter new courses path: "
        )
    ) ELSE (
        SET /P COURSES_PATH="  Enter the path to your courses directory: "
    )
)

IF "%COURSES_PATH%"=="" (
    echo   ERROR: No courses path provided.
    pause & EXIT /B 1
)

IF NOT EXIST "!COURSES_PATH!\" (
    echo   WARNING: Path "!COURSES_PATH!" does not exist or is not accessible.
    SET /P CONFIRM="  Continue anyway? [y/N]: "
    IF /I NOT "!CONFIRM!"=="y" (
        echo   Aborted.
        pause & EXIT /B 1
    )
)

echo   OK  Courses path: !COURSES_PATH!

:: Write / update .env file
IF EXIST "%ENV_FILE%" (
    :: Replace COURSES_PATH line using Python (handles edge cases better than pure batch)
    python -c "
import re, sys
path = sys.argv[1]
with open(sys.argv[2], 'r') as f: content = f.read()
if re.search(r'^COURSES_PATH=', content, re.MULTILINE):
    content = re.sub(r'^COURSES_PATH=.*', 'COURSES_PATH=' + path, content, flags=re.MULTILINE)
else:
    content += '\nCOURSES_PATH=' + path + '\n'
with open(sys.argv[2], 'w') as f: f.write(content)
" "!COURSES_PATH!" "%ENV_FILE%" 2>nul
    IF ERRORLEVEL 1 (
        echo COURSES_PATH=!COURSES_PATH!>> "%ENV_FILE%"
    )
    echo   OK  .env updated
) ELSE (
    IF EXIST "%SCRIPT_DIR%\.env.example" (
        COPY "%SCRIPT_DIR%\.env.example" "%ENV_FILE%" >nul
    ) ELSE (
        (
            echo COURSES_PATH=!COURSES_PATH!
            echo PORT=5000
            echo ES_PORT=9200
            echo KIBANA_PORT=5601
            echo DEBUG=false
            echo MAX_RESULTS=50
            echo ES_INDEX=course_catalog
        ) > "%ENV_FILE%"
    )
    python -c "
import re, sys
path = sys.argv[1]
with open(sys.argv[2], 'r') as f: content = f.read()
content = re.sub(r'^COURSES_PATH=.*', 'COURSES_PATH=' + path, content, flags=re.MULTILINE)
with open(sys.argv[2], 'w') as f: f.write(content)
" "!COURSES_PATH!" "%ENV_FILE%" 2>nul
    echo   OK  .env created
)

:: =============================================================================
:: STEP 3 — (Optional) Generate catalog HTML locally
:: =============================================================================
IF /I "!DO_GENERATE!"=="true" (
    echo.
    echo [Step 3/6] Generating Course Catalog (optional)...

    SET "LOCAL_VENV=%SCRIPT_DIR%\.venv\Scripts\activate.bat"
    IF EXIST "!LOCAL_VENV!" (
        CALL "!LOCAL_VENV!"
        python -c "import flask" >nul 2>&1
        IF NOT ERRORLEVEL 1 (
            cd /D "%SCRIPT_DIR%"
            python "%SCRIPT_DIR%\..\backend\catalog_generator.py" "!COURSES_PATH!" --output "%SCRIPT_DIR%\..\output"
            IF NOT ERRORLEVEL 1 (
                echo   OK  Catalog generated: output\course_catalog.html
            ) ELSE (
                echo   WARNING: Catalog generation failed.
                echo   Run start.bat first to generate the catalog.
            )
        ) ELSE (
            echo   Dependencies not installed -- skipping catalog generation.
            echo   Run start.bat first (native) to generate the catalog.
        )
    ) ELSE (
        python "%SCRIPT_DIR%\..\backend\catalog_generator.py" "!COURSES_PATH!" --output "%SCRIPT_DIR%\..\output" 2>nul
        IF NOT ERRORLEVEL 1 (
            echo   OK  Catalog generated: output\course_catalog.html
        ) ELSE (
            echo   Python/dependencies not available -- skipping catalog generation.
            echo   Run start.bat first to generate the catalog.
        )
    )
) ELSE (
    echo [Step 3/6] (Skipping catalog generation -- --no-generate)
)

:: =============================================================================
:: STEP 4 — Build and start Docker services
:: =============================================================================
echo.
echo [Step 4/6] Starting Docker Services...

cd /D "%SCRIPT_DIR%"

IF /I "!WITH_KIBANA!"=="true" (
    echo   Starting: backend + Elasticsearch + Kibana
    !COMPOSE_CMD! --profile kibana up -d --build
) ELSE (
    echo   Starting: backend + Elasticsearch
    !COMPOSE_CMD! up -d --build
)
IF ERRORLEVEL 1 (
    echo   ERROR: docker-compose failed.
    pause & EXIT /B 1
)

:: Wait for Elasticsearch to be ready
echo   Waiting for Elasticsearch to be ready...
SET "MAX_WAIT=120"
SET "WAITED=0"
:WAIT_ES
    timeout /t 5 /nobreak >nul
    SET /A WAITED+=5
    curl -sf "http://127.0.0.1:9200/_cluster/health" >nul 2>&1
    IF NOT ERRORLEVEL 1 GOTO :ES_READY
    IF !WAITED! GEQ !MAX_WAIT! (
        echo   WARNING: ES health check timed out. It may still be starting.
        GOTO :ES_READY
    )
    <nul SET /P ".=  ."
    GOTO :WAIT_ES
:ES_READY
echo.
echo   OK  Elasticsearch ready
echo   OK  Backend API: http://localhost:5000
IF /I "!WITH_KIBANA!"=="true" echo   OK  Kibana: http://localhost:5601

:: =============================================================================
:: STEP 5 — Elasticsearch indexing
:: =============================================================================
IF /I "!DO_INDEX!"=="true" (
    echo.
    echo [Step 5/6] Elasticsearch Indexing...

    SET "DOC_COUNT=0"
    FOR /F "tokens=*" %%C IN (
        'curl -sf "http://127.0.0.1:9200/course_catalog/_count" 2^>nul'
    ) DO (
        FOR /F "tokens=2 delims=:}" %%N IN ('echo %%C ^| findstr /C:"count"') DO (
            SET "DOC_COUNT=%%N"
        )
    )

    IF "!DOC_COUNT!" GTR "0" (
        echo.
        echo   The Elasticsearch index already contains !DOC_COUNT! document(s).
        echo.
        SET /P RESET_CHOICE="  Reset the index and re-index all files from scratch? [y/N]: "
        IF /I "!RESET_CHOICE!"=="y" (
            echo   Re-indexing with --reset (deletes existing data)...
            !COMPOSE_CMD! run --rm indexer python backend/search_indexer.py --reset
        ) ELSE (
            SET /P INC_CHOICE="  Run incremental index (add new files)? [Y/n]: "
            IF /I NOT "!INC_CHOICE!"=="n" (
                echo   Running incremental indexer...
                !COMPOSE_CMD! run --rm indexer
            ) ELSE (
                echo   Skipping indexing. Existing index will be used.
            )
        )
    ) ELSE (
        echo   Index is empty -- running full indexer now...
        echo   (This may take a few minutes depending on the number of files)
        !COMPOSE_CMD! run --rm indexer
    )
    echo   OK  Elasticsearch indexing complete
) ELSE (
    echo [Step 5/6] (Skipping indexing -- --no-index)
)

:: =============================================================================
:: STEP 6 — Usage summary and test examples
:: =============================================================================
echo.
echo [Step 6/6] Ready!
echo.
echo   ====================================================
echo    Services running:
echo.
echo    Backend API   --^>  http://localhost:5000
echo    Elasticsearch --^>  http://127.0.0.1:9200
IF /I "!WITH_KIBANA!"=="true" (
    echo    Kibana        --^>  http://localhost:5601
)
echo.
echo    Catalog HTML  --^>  %SCRIPT_DIR%\course_catalog.html
echo    (open this file in your browser)
echo   ====================================================
echo.
echo   Test the backend API (run in another terminal window):
echo.
echo     REM Health check (shows ES connection status)
echo     curl http://localhost:5000/api/health
echo.
echo     REM Standard full-text search
echo     curl "http://localhost:5000/api/search?q=Installation"
echo.
echo     REM ES BM25 search with German language analyzer
echo     curl "http://localhost:5000/api/es-search?q=Benutzer&lang=de"
echo.
echo     REM ES semantic search (kNN vector similarity)
echo     curl "http://localhost:5000/api/es-search?q=user+administration&semantic=1"
echo.
echo     REM ES hybrid search (BM25 + semantic, Spanish)
echo     curl "http://localhost:5000/api/es-search?q=abreviatura&lang=es&semantic=1"
echo.
echo     REM Count indexed documents in Elasticsearch
echo     curl "http://127.0.0.1:9200/course_catalog/_count?pretty"
echo.
echo     REM Elasticsearch cluster health
echo     curl "http://127.0.0.1:9200/_cluster/health?pretty"
echo.
echo   ====================================================
echo   Docker management:
echo.
echo     !COMPOSE_CMD! ps              -- show running services
echo     !COMPOSE_CMD! logs -f backend -- follow backend logs
echo     !COMPOSE_CMD! down            -- stop all services
echo     !COMPOSE_CMD! down -v         -- stop + delete ES data
echo   ====================================================
echo.

endlocal
pause
