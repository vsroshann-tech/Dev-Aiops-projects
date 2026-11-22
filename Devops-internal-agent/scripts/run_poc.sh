#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
  echo "Virtual environment not found. Run ./scripts/setup_ubuntu.sh first."
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Starting Internal DevOps AI Agent on http://0.0.0.0:8005"
echo "==> Open http://localhost:8005 in your browser for local POC chat & doc upload,"
echo "==> or connect Retool using retool/RETOOL_SETUP_GUIDE.md."
exec uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload
