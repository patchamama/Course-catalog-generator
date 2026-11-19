#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -d "venv" ]]; then
  echo "[INFO] Creando entorno virtual en ./venv"
  python3 -m venv venv
fi

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  if [[ "$(cd "$VIRTUAL_ENV" && pwd)" == "$(cd "venv" && pwd)" ]]; then
    echo "[INFO] Ya estás dentro del venv del proyecto: $VIRTUAL_ENV"
  else
    echo "[INFO] Venv activo detectado: $VIRTUAL_ENV"
    echo "[INFO] Se usará ese entorno activo (no se activará ./venv)."
  fi
else
  # shellcheck disable=SC1091
  source venv/bin/activate
  echo "[INFO] Entorno activado: $VIRTUAL_ENV"
fi

python -m pip install --upgrade pip >/dev/null
if [[ -f "requirements.txt" ]]; then
  pip install -r requirements.txt >/dev/null
fi

PORT="$(python - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
URL="http://127.0.0.1:${PORT}"

echo "[INFO] Iniciando servidor web..."
echo "[INFO] URL: ${URL}"

# Abrir navegador (best effort)
if command -v xdg-open >/dev/null 2>&1; then
  (sleep 1; xdg-open "${URL}" >/dev/null 2>&1 || true) &
elif command -v open >/dev/null 2>&1; then
  (sleep 1; open "${URL}" >/dev/null 2>&1 || true) &
elif command -v cmd.exe >/dev/null 2>&1; then
  (sleep 1; cmd.exe /C start "" "${URL}" >/dev/null 2>&1 || true) &
fi

exec python webapp.py --port "${PORT}"
