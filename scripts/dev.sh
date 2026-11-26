#!/usr/bin/env bash
# Development launcher for macOS/Linux and Git Bash.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${1:-8765}"

if [ ! -d ".venv" ]; then
  echo "==> installing python dependencies"
  uv sync --all-extras
fi

if [ ! -d "frontend/node_modules" ]; then
  echo "==> installing frontend dependencies"
  (cd frontend && npm install)
fi

if [ ! -f "frontend/dist/index.html" ]; then
  echo "==> building frontend"
  (cd frontend && npm run build)
fi

echo "==> starting EduCore on http://127.0.0.1:${PORT}"
exec uv run python -m backend.main --port "$PORT"
