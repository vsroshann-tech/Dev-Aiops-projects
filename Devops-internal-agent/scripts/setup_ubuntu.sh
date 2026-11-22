#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> Setting up DevOps AI Agent POC in: $PROJECT_DIR"

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example"
fi

if ! python3 -m venv --help >/dev/null 2>&1 || ! python3 -m pip --version >/dev/null 2>&1; then
  echo "==> Installing python3-venv and python3-pip on Ubuntu (may prompt for sudo)..."
  sudo apt-get update -y && sudo apt-get install -y python3-venv python3-pip
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "==> Preloading non-secret sample_docs/ into local RAG vector store..."
python -m scripts.ingest_sample_docs

echo "==> Running automated compliance & unit tests..."
pytest -q

echo ""
echo "Setup complete! Start the POC server with:"
echo "  ./scripts/run_poc.sh"
