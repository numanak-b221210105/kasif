#!/usr/bin/env bash
# Kasif - macOS / Linux icin tek komutla kurulum ve calistirma
set -euo pipefail
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python) || { echo "Python 3.11+ gerekli"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "[1/2] Sanal ortam olusturuluyor..."
  "$PY" -m venv .venv
fi
if ! ls .venv/lib/python*/site-packages/kasif-*.dist-info >/dev/null 2>&1; then
  echo "[2/2] Paketler yukleniyor..."
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -e ".[dev]"
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python -m kasif.serve "$@"
