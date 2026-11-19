@echo off
setlocal enabledelayedexpansion

REM Ir al directorio del script
cd /d "%~dp0"

REM Crear venv si no existe
if not exist "venv\Scripts\python.exe" (
  echo [INFO] Creando entorno virtual en .\venv
  python -m venv venv
)

REM Detectar si ya hay un venv activo
if defined VIRTUAL_ENV (
  for %%I in ("%VIRTUAL_ENV%") do set "ACTIVE_VENV=%%~fI"
  for %%I in ("%CD%\venv") do set "PROJECT_VENV=%%~fI"

  if /I "!ACTIVE_VENV!"=="!PROJECT_VENV!" (
    echo [INFO] Ya estas dentro del venv del proyecto: %VIRTUAL_ENV%
  ) else (
    echo [INFO] Venv activo detectado: %VIRTUAL_ENV%
    echo [INFO] Se usara ese entorno activo ^(no se activara .\venv^).
  )
) else (
  call "venv\Scripts\activate.bat"
  echo [INFO] Entorno activado: %VIRTUAL_ENV%
)

python -m pip install --upgrade pip >nul 2>&1
if exist requirements.txt (
  pip install -r requirements.txt >nul 2>&1
)

echo [INFO] Iniciando servidor web...
for /f %%P in ('python -c "import socket; s=socket.socket(); s.bind((\"127.0.0.1\",0)); print(s.getsockname()[1]); s.close()"') do set "PORT=%%P"
set "URL=http://127.0.0.1:%PORT%"
echo [INFO] URL: %URL%
start "" "%URL%"

python webapp.py --port %PORT%
