@echo off
:: mdfromhtml — HTML to Markdown pipeline
::
:: Pipeline per source folder:
::   Step 1  html-to-md.mjs       <source>\  →  _output\1_raw\
::   Step 2  build-collection.mjs <source>\  →  _output\2_collection\
::   Step 3  merge-book.mjs       _output\2_collection\  →  _output\book_full.md + _output\<source>.md
::
:: Final: all book_full.md files merged into _all_books.md at project root
::
:: Skip logic:
::   - _output\book_full.md exists → folder fully done, skip entirely (and sync _output\<source>.md)
::   - _output\2_collection\ has .md files → steps 1+2 done, resume at step 3
::   - _output\1_raw\ has .md files        → step 1 done, resume at step 2
::   - Folders with _ or . prefix, node_modules, or no root-level HTML are excluded
::
:: Requirements: Node.js 18+, pandoc

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo mdfromhtml -- HTML to Markdown pipeline
echo =======================================
echo.

:: ── Prerequisites ────────────────────────────────────────────────────────────

echo Checking prerequisites...

where node >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Node.js not found. Install 18+ from https://nodejs.org
    exit /b 1
)
for /f "tokens=1" %%v in ('node --version 2^>nul') do set "NODE_VER=%%v"
echo   Node.js !NODE_VER! OK

where pandoc >nul 2>&1
if errorlevel 1 (
    echo   ERROR: pandoc not found. Install from https://github.com/jgm/pandoc/releases
    exit /b 1
)
for /f "tokens=1,2" %%a in ('pandoc --version 2^>nul') do (
    echo   %%a %%b OK
    goto :prereqs_ok
)
:prereqs_ok
echo.

:: ── Tracking variables ────────────────────────────────────────────────────────

set "PROCESSED_COUNT=0"
set "RESUMED_COUNT=0"
set "SKIPPED_COUNT=0"
set "FAILED_COUNT=0"
set "FAILED_LIST="

:: Temp file to collect book paths for final aggregation
set "BOOKS_TMP=%TEMP%\mdfromhtml_books_%RANDOM%.tmp"
if exist "!BOOKS_TMP!" del "!BOOKS_TMP!"

:: ── Per-folder pipeline ───────────────────────────────────────────────────────

for /D %%I in (*) do (
    call :process_folder "%%~I" "%%~nxI"
)

:: ── Final unified markdown ────────────────────────────────────────────────────

echo.
echo ===============================================================

if not exist "!BOOKS_TMP!" goto :summary

:: Count books
set "BOOK_COUNT=0"
for /f "usebackq tokens=*" %%L in ("!BOOKS_TMP!") do set /a BOOK_COUNT+=1

if !BOOK_COUNT! == 0 goto :summary

echo   Building _all_books.md  (!BOOK_COUNT! book(s))...

set "FINAL=_all_books.md"
if exist "%FINAL%" del "%FINAL%"

>> "%FINAL%" echo # All Books
>> "%FINAL%" echo.
for /f "tokens=2 delims=|" %%D in ('wmic os get LocalDateTime /value ^| findstr "LocalDateTime"') do (
    set "DT=%%D"
    >> "%FINAL%" echo ^> Generated: !DT:~0,4!-!DT:~4,2!-!DT:~6,2! !DT:~8,2!:!DT:~10,2!:!DT:~12,2!  ^|  !BOOK_COUNT! book(s)
)
>> "%FINAL%" echo.
>> "%FINAL%" echo ---

for /f "usebackq tokens=1,2 delims=|" %%N in ("!BOOKS_TMP!") do (
    >> "%FINAL%" echo.
    >> "%FINAL%" echo # %%N
    >> "%FINAL%" echo.
    type "%%O" >> "%FINAL%"
    >> "%FINAL%" echo.
    >> "%FINAL%" echo ---
)

echo   -^> %FINAL%

:summary
echo.
echo   Processed:    !PROCESSED_COUNT!  (new)
if !RESUMED_COUNT! gtr 0 echo   Resumed:      !RESUMED_COUNT!  (partial -^> completed)
if !SKIPPED_COUNT! gtr 0 echo   Already done: !SKIPPED_COUNT!  (skipped)
if !FAILED_COUNT! gtr 0 (
    echo.
    echo   FAILED (!FAILED_COUNT!):
    echo   !FAILED_LIST!
)
echo ===============================================================
echo.
echo Done.

if exist "!BOOKS_TMP!" del "!BOOKS_TMP!"
endlocal
exit /b 0

:: ════════════════════════════════════════════════════════════════════════════
:: Subroutine: process_folder  %1=path  %2=display_name
:: ════════════════════════════════════════════════════════════════════════════
:process_folder
set "FPATH=%~1"
set "FNAME=%~2"

:: Exclude internal, hidden, and non-source dirs
if "!FNAME:~0,1!" == "." exit /b 0
if "!FNAME:~0,1!" == "_" exit /b 0
if /i "!FNAME!" == "node_modules" exit /b 0

:: Only process folders with HTML files directly inside them
dir /b /a-d "!FPATH!\*.html" >nul 2>&1
if errorlevel 1 exit /b 0

set "OUT=!FPATH!\_output"
set "STEP1=!OUT!\1_raw"
set "STEP2=!OUT!\2_collection"
set "BOOK=!OUT!\book_full.md"
set "NAMED_BOOK=!OUT!\!FNAME!.md"

:: ── Fully done: skip ────────────────────────────────────────────────────────
if exist "!BOOK!" (
    copy /Y "!BOOK!" "!NAMED_BOOK!" >nul
    echo   !FNAME! ... [done - skipped]
    >> "!BOOKS_TMP!" echo !FNAME!^|!BOOK!
    set /a SKIPPED_COUNT+=1
    exit /b 0
)

:: ── Detect partial completion (resume support) ──────────────────────────────
set "STEP1_DONE=0"
set "STEP2_DONE=0"
dir /b /a-d "!STEP1!\*.md" >nul 2>&1 && set "STEP1_DONE=1"
dir /b /a-d "!STEP2!\*.md" >nul 2>&1 && set "STEP2_DONE=1"

set "IS_RESUME=0"
if !STEP1_DONE! == 1 set "IS_RESUME=1"
if !STEP2_DONE! == 1 set "IS_RESUME=1"

:: Count HTML files
set "HTML_COUNT=0"
for %%F in ("!FPATH!\*.html") do set /a HTML_COUNT+=1

echo +--------------------------------------------------------------
echo ^|  !FNAME!  (!HTML_COUNT! HTML files)
if !IS_RESUME! == 1 echo ^|  Resuming from partial completion...
echo +--------------------------------------------------------------

if not exist "!STEP1!" mkdir "!STEP1!"
if not exist "!STEP2!" mkdir "!STEP2!"

:: ── Step 1: HTML → raw Markdown ─────────────────────────────────────────────
:: Input:  <source>\*.html
:: Output: _output\1_raw\*.md  (one per HTML, images in subfolders)
if !STEP1_DONE! == 1 (
    echo   [1/3] html-to-md ......... already done
) else (
    echo   [1/3] html-to-md ...
    node html-to-md.mjs "!FPATH!" "!STEP1!"
    if errorlevel 1 (
        echo         FAILED -- skipping folder
        set /a FAILED_COUNT+=1
        set "FAILED_LIST=!FAILED_LIST! / !FNAME! (step 1: html-to-md)"
        exit /b 0
    )
    echo         -^> !STEP1!
)

:: ── Step 2: HTML → quality Markdown collection ──────────────────────────────
:: Input:  <source>\*.html  (build-collection.mjs requires HTML, not MDs)
:: Output: _output\2_collection\*.md + content.md + book.md + 99.videos.md
:: Note:   This output feeds Step 3.
if !STEP2_DONE! == 1 (
    echo   [2/3] build-collection ... already done
) else (
    echo   [2/3] build-collection ...
    node build-collection.mjs "!FPATH!" "!STEP2!"
    if errorlevel 1 (
        echo         FAILED -- skipping folder
        set /a FAILED_COUNT+=1
        set "FAILED_LIST=!FAILED_LIST! / !FNAME! (step 2: build-collection)"
        exit /b 0
    )
    echo         -^> !STEP2!
)

:: ── Step 3: quality Markdown → merged single book ───────────────────────────
:: Input:  _output\2_collection\  (step 2 output)
:: Output: _output\book_full.md + _output\<source>.md + _output\images\ (consolidated)
echo   [3/3] merge-book ...
node merge-book.mjs "!STEP2!" --output "!BOOK!" --images-dest "!OUT!\images"
if errorlevel 1 (
    echo         FAILED -- skipping folder
    set /a FAILED_COUNT+=1
    set "FAILED_LIST=!FAILED_LIST! / !FNAME! (step 3: merge-book)"
    exit /b 0
)
copy /Y "!BOOK!" "!NAMED_BOOK!" >nul
echo         -^> !BOOK!
echo         -^> !NAMED_BOOK!

if exist "!STEP2!\99.videos.md" echo         -^> !STEP2!\99.videos.md (video index)
echo.

>> "!BOOKS_TMP!" echo !FNAME!^|!BOOK!

if !IS_RESUME! == 1 (
    set /a RESUMED_COUNT+=1
) else (
    set /a PROCESSED_COUNT+=1
)
exit /b 0
