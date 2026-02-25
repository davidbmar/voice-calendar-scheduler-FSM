#!/bin/bash
# Start the voice calendar scheduler server
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/engine-repo"

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
VENV_UVICORN="$PROJECT_ROOT/.venv/bin/uvicorn"

if [ ! -f "$VENV_UVICORN" ]; then
    echo "venv not found. Run ./scripts/setup.sh first."
    exit 1
fi

# Optionally start RAG service
if command -v docker &>/dev/null && [ -f docker-compose.yml ]; then
    echo "Starting RAG service..."
    docker compose up -d rag 2>/dev/null || echo "RAG not available (skipping)"
fi

PORT="${PORT:-8090}"
HOST="${HOST:-127.0.0.1}"

echo "Starting server on http://$HOST:$PORT"
echo "  Browser client: http://localhost:$PORT"
echo "  Admin panel:    http://localhost:$PORT/admin"
echo "  Health check:   http://localhost:$PORT/health"
echo ""

exec "$VENV_UVICORN" scheduling.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload
