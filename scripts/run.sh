#!/bin/bash
# Start the voice calendar scheduler server
# Usage: ./scripts/run.sh [--tunnel]
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

# ── Parse args ────────────────────────────────────────────────────────
USE_TUNNEL=false
for arg in "$@"; do
    case "$arg" in
        --tunnel) USE_TUNNEL=true ;;
    esac
done

# ── Source PORT/HOST from .env if not already set ─────────────────────
if [ -f "$PROJECT_ROOT/.env" ]; then
    # Only import PORT and HOST, don't pollute env with everything
    _env_port=$(grep -E '^PORT=' "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2-)
    _env_host=$(grep -E '^HOST=' "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2-)
    [ -n "$_env_port" ] && PORT="${PORT:-$_env_port}"
    [ -n "$_env_host" ] && HOST="${HOST:-$_env_host}"
fi
PORT="${PORT:-9909}"
HOST="${HOST:-127.0.0.1}"

# Optionally start RAG service
if command -v docker &>/dev/null && [ -f docker-compose.yml ]; then
    echo "Starting RAG service..."
    docker compose up -d rag 2>/dev/null || echo "RAG not available (skipping)"
fi

echo "Starting server on http://$HOST:$PORT"
echo "  Browser client: http://localhost:$PORT"
echo "  Admin panel:    http://localhost:$PORT/admin"
echo "  Health check:   http://localhost:$PORT/health"

# ── Without tunnel: simple exec (original behavior) ──────────────────
if [ "$USE_TUNNEL" = false ]; then
    echo ""
    exec "$VENV_UVICORN" scheduling.app:app \
        --host "$HOST" \
        --port "$PORT" \
        --reload
fi

# ── With tunnel ───────────────────────────────────────────────────────
if ! command -v cloudflared &>/dev/null; then
    echo "cloudflared not found. Install with:"
    echo "  brew install cloudflare/cloudflare/cloudflared"
    exit 1
fi

CONFIG_FILE="$PROJECT_ROOT/.tunnel-config"
CLOUDFLARED_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    if [ -n "$CLOUDFLARED_PID" ] && kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
        kill "$CLOUDFLARED_PID" 2>/dev/null
        wait "$CLOUDFLARED_PID" 2>/dev/null || true
        echo "Cloudflared stopped."
    fi
}
trap cleanup EXIT

if [ -f "$CONFIG_FILE" ]; then
    # Named tunnel from setup_tunnel.sh
    source "$CONFIG_FILE"
    echo "  Tunnel:         ${TUNNEL_URL:-$TUNNEL_NAME}"
    echo ""
    cloudflared tunnel --url "http://localhost:$PORT" run "$TUNNEL_NAME" &
    CLOUDFLARED_PID=$!
else
    # Quick tunnel — random trycloudflare.com URL
    echo "  Tunnel:         (quick tunnel — URL will appear below)"
    echo ""
    echo "No .tunnel-config found — using quick tunnel."
    echo "Run ./scripts/setup_tunnel.sh for a stable URL."
    echo ""
    cloudflared tunnel --url "http://localhost:$PORT" &
    CLOUDFLARED_PID=$!
fi

# Run uvicorn as a child process (not exec) so the EXIT trap fires
"$VENV_UVICORN" scheduling.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload
