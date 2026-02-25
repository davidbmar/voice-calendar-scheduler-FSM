#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Voice Calendar Scheduler
# ─────────────────────────────────────────────────────────────────────────────
#
# This is the single entry point for running the Voice Calendar Scheduler.
# It brings up every service you need in the right order, validates your
# configuration along the way, and shows you a status dashboard when
# everything is ready.
#
# What it starts:
#
#   1. RAG Service        A Docker container that powers apartment search.
#                         It loads listing data into a vector database so
#                         callers can search by natural language
#                         ("2 bedroom near downtown under $2000").
#
#   2. Backend            The FastAPI server — the core of the application.
#                         Handles Twilio phone calls, browser WebRTC,
#                         the 8-step FSM conversation engine, LLM calls,
#                         speech-to-text, text-to-speech, and all APIs.
#
#   3. Editor             A Vite dev server for the visual workflow editor.
#                         This is the drag-and-drop UI for editing FSM
#                         states, prompts, and transitions. Only needed
#                         during development — in production, the editor
#                         is pre-built and served by the backend.
#
# Before you run this:
#
#   1. Run ./scripts/setup.sh first (creates venv, installs dependencies)
#   2. Copy .env.example to .env and fill in your API keys
#   3. For the editor: cd web/editor && npm install
#
# Usage:
#   ./scripts/start.sh              # Start everything
#   ./scripts/start.sh --no-rag     # Skip the RAG Docker container
#   ./scripts/start.sh --no-editor  # Skip the editor dev server
#   ./scripts/start.sh --check      # Just validate config, don't start
#
# The script pauses between each component so you can see what's happening.
# Press Enter to move to the next step, or Ctrl+C at any time to stop.
# ─────────────────────────────────────────────────────────────────────────────

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# ── Colors & formatting ──────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
step() { echo -e "\n${BOLD}── $1 ──${NC}"; }

pause_continue() {
    echo ""
    echo -e "  ${DIM}Press Enter to continue...${NC}"
    read -r
}

# ── Process tracking ─────────────────────────────────────────
BACKEND_PID=""
EDITOR_PID=""

cleanup() {
    echo ""
    echo -e "${DIM}Shutting down...${NC}"
    [ -n "$EDITOR_PID" ] && kill "$EDITOR_PID" 2>/dev/null && echo -e "  ${DIM}Editor stopped${NC}"
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null && echo -e "  ${DIM}Backend stopped${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Parse flags ──────────────────────────────────────────────
SKIP_RAG=false
SKIP_EDITOR=false
CHECK_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --no-rag)    SKIP_RAG=true ;;
        --no-editor) SKIP_EDITOR=true ;;
        --check)     CHECK_ONLY=true ;;
    esac
done

echo ""
echo -e "${BOLD}═══ Voice Calendar Scheduler ═══${NC}"
echo ""
echo -e "  This script validates your environment and starts three components:"
echo ""
echo -e "    ${CYAN}1. RAG${NC}       Apartment search (Docker)     Enables natural language listing search"
echo -e "    ${CYAN}2. Backend${NC}   FastAPI + voice pipeline      Handles calls, FSM, LLM, STT/TTS"
echo -e "    ${CYAN}3. Editor${NC}    Vite dev server               Visual workflow editor (dev only)"
echo ""
echo -e "  ${DIM}Each step pauses so you can review. Press Enter to continue, Ctrl+C to stop.${NC}"

# ── Step 1: Validate .env ────────────────────────────────────
step "Environment"
echo ""
echo -e "  ${DIM}Checking your .env file for required API keys and configuration.${NC}"
echo -e "  ${DIM}The app needs at minimum an LLM provider key (Claude or Ollama).${NC}"
echo -e "  ${DIM}Optional: ADMIN_API_KEY (secures admin endpoints), Twilio, Google Calendar.${NC}"
echo ""

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    fail ".env not found. Copy .env.example → .env and configure it."
    exit 1
fi
ok ".env exists"

# Source .env for shell-level checks
set -a
source "$PROJECT_ROOT/.env" 2>/dev/null || true
set +a

# Check LLM key
if [ "${LLM_PROVIDER:-claude}" = "claude" ]; then
    if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "sk-ant-..." ]; then
        fail "ANTHROPIC_API_KEY is missing or placeholder. Set it in .env."
        exit 1
    fi
    ok "LLM: Claude API key present"
else
    ok "LLM: Ollama (no API key needed)"
fi

# Warn on missing admin key
if [ -z "$ADMIN_API_KEY" ]; then
    if [ "${DEBUG:-false}" = "true" ]; then
        warn "ADMIN_API_KEY not set — admin APIs open (DEBUG=true)"
    else
        warn "ADMIN_API_KEY not set — admin APIs locked in production"
        warn "  Generate one: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
    fi
else
    ok "Admin API key configured"
fi

# Warn on placeholder integrations
if [ "$TWILIO_ACCOUNT_SID" = "AC..." ] || [ -z "$TWILIO_ACCOUNT_SID" ]; then
    warn "Twilio not configured (PSTN calls won't work)"
fi
if [ "$GOOGLE_SERVICE_ACCOUNT_JSON" = "path/to/service-account.json" ] || [ -z "$GOOGLE_SERVICE_ACCOUNT_JSON" ]; then
    warn "Google Calendar not configured (bookings will be mocked)"
fi

# ── Step 2: Check dependencies ───────────────────────────────
step "Dependencies"
echo ""
echo -e "  ${DIM}Verifying that all required components are installed and ready.${NC}"
echo -e "  ${DIM}This checks for the Python virtual environment (.venv), the engine${NC}"
echo -e "  ${DIM}submodule (LLM + STT + TTS runtime), and the editor's node_modules.${NC}"
echo -e "  ${DIM}If anything is missing, you'll see instructions on how to fix it.${NC}"
echo ""

VENV_UVICORN="$PROJECT_ROOT/.venv/bin/uvicorn"
if [ ! -f "$VENV_UVICORN" ]; then
    fail "venv not found. Run ./scripts/setup.sh first."
    exit 1
fi
ok "Python venv"

if [ ! -f "$PROJECT_ROOT/engine-repo/engine/orchestrator.py" ]; then
    fail "Engine submodule missing. Run: git submodule update --init --recursive"
    exit 1
fi
ok "Engine submodule"

# Check editor node_modules
EDITOR_DIR="$PROJECT_ROOT/web/editor"
HAS_EDITOR=false
if [ -d "$EDITOR_DIR/node_modules" ] && [ -f "$EDITOR_DIR/package.json" ]; then
    HAS_EDITOR=true
    ok "Editor node_modules"
elif [ "$SKIP_EDITOR" = false ]; then
    warn "Editor node_modules missing (run: cd web/editor && npm install)"
    warn "  Skipping editor dev server"
    SKIP_EDITOR=true
fi

# ── Step 3: Python-level validation ──────────────────────────
step "Config validation"
echo ""
echo -e "  ${DIM}Running Python-level checks on your Pydantic settings.${NC}"
echo -e "  ${DIM}This catches issues that shell checks can't — like an API key that${NC}"
echo -e "  ${DIM}exists but is malformed, or config values that conflict with each other.${NC}"
echo -e "  ${DIM}Any warnings here are non-fatal; errors will stop the script.${NC}"
echo ""

export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/engine-repo"
"$PROJECT_ROOT/.venv/bin/python" -c "
from scheduling.config import settings
warnings = settings.validate_startup()
for w in warnings:
    print(f'  ! {w}')
if not warnings:
    print('  All checks passed')
" 2>&1 || { fail "Config validation failed"; exit 1; }

# ── Stop here if --check ─────────────────────────────────────
if [ "$CHECK_ONLY" = true ]; then
    echo ""
    ok "Validation complete (--check mode, not starting)"
    exit 0
fi

pause_continue

# ── Component status tracking ────────────────────────────────
RAG_STATUS="${DIM}skipped${NC}"
BACKEND_STATUS="${DIM}not started${NC}"
EDITOR_STATUS="${DIM}skipped${NC}"
RAG_URL=""
BACKEND_URL=""
EDITOR_URL=""

PORT="${PORT:-8090}"
HOST="${HOST:-127.0.0.1}"

# ── Step 4: Start RAG ────────────────────────────────────────
if [ "$SKIP_RAG" = false ] && command -v docker &>/dev/null && [ -f docker-compose.yml ]; then
    step "1/3  RAG Service (apartment search)"
    echo ""
    echo -e "  ${DIM}The RAG (Retrieval-Augmented Generation) service powers natural language${NC}"
    echo -e "  ${DIM}apartment search. It runs as a Docker container with a vector database${NC}"
    echo -e "  ${DIM}that lets callers search listings conversationally — e.g., \"2 bedroom${NC}"
    echo -e "  ${DIM}near downtown under \$2000\". The service path is read from services.conf.${NC}"
    echo -e "  ${DIM}If Docker isn't available or the repo is missing, the app continues${NC}"
    echo -e "  ${DIM}without search — everything else still works.${NC}"
    echo ""

    # Read RAG path from services.conf
    RAG_PATH=""
    if [ -f "$PROJECT_ROOT/services.conf" ]; then
        RAG_PATH=$(grep -A1 '^\[rag\]' "$PROJECT_ROOT/services.conf" | grep '^path=' | cut -d= -f2)
    fi
    RAG_PATH="${RAG_PATH:-../2026-feb-voice-optimal-RAG}"

    if [ -d "$PROJECT_ROOT/$RAG_PATH" ]; then
        export RAG_BUILD_CONTEXT="$RAG_PATH"
        echo "  Starting from ${CYAN}$RAG_PATH${NC}..."
        docker compose up -d rag 2>/dev/null || { warn "Docker failed (continuing without RAG)"; RAG_STATUS="${YELLOW}failed${NC}"; }

        if [ "$RAG_STATUS" != "${YELLOW}failed${NC}" ]; then
            # Wait for health (up to 60s)
            echo -n "  Waiting for health"
            RAG_HEALTHY=false
            for i in $(seq 1 60); do
                if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
                    echo ""
                    ok "RAG healthy on :8000"
                    RAG_STATUS="${GREEN}healthy${NC}"
                    RAG_URL="http://localhost:8000"
                    RAG_HEALTHY=true
                    break
                fi
                echo -n "."
                sleep 1
            done
            if [ "$RAG_HEALTHY" = false ]; then
                echo ""
                warn "Health check timed out (continuing without RAG)"
                RAG_STATUS="${YELLOW}timeout${NC}"
            fi
        fi
    else
        warn "RAG repo not found at $RAG_PATH"
        RAG_STATUS="${YELLOW}not found${NC}"
    fi

    pause_continue
else
    if [ "$SKIP_RAG" = true ]; then
        RAG_STATUS="${DIM}skipped (--no-rag)${NC}"
    fi
fi

# ── Step 5: Start Backend ────────────────────────────────────
step "2/3  Backend (FastAPI + voice pipeline)"
echo ""
echo -e "  ${DIM}This is the core of the application — the FastAPI server that handles${NC}"
echo -e "  ${DIM}everything: Twilio phone calls, browser WebRTC audio, the FSM${NC}"
echo -e "  ${DIM}conversation engine, LLM calls, speech-to-text, text-to-speech,${NC}"
echo -e "  ${DIM}and all admin/API endpoints. It runs with --reload so code changes${NC}"
echo -e "  ${DIM}take effect immediately during development.${NC}"
echo ""

echo "  Starting uvicorn on ${CYAN}$HOST:$PORT${NC}..."
"$VENV_UVICORN" scheduling.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload \
    --log-level info &
BACKEND_PID=$!

# Wait for backend health
echo -n "  Waiting for health"
BACKEND_HEALTHY=false
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
        echo ""
        ok "Backend healthy on :$PORT"
        BACKEND_STATUS="${GREEN}healthy${NC}"
        BACKEND_URL="http://localhost:$PORT"
        BACKEND_HEALTHY=true
        break
    fi
    echo -n "."
    sleep 1
done
if [ "$BACKEND_HEALTHY" = false ]; then
    echo ""
    fail "Backend failed to start within 30s"
    BACKEND_STATUS="${RED}failed${NC}"
    cleanup
    exit 1
fi

pause_continue

# ── Step 6: Start Editor ─────────────────────────────────────
if [ "$SKIP_EDITOR" = false ] && [ "$HAS_EDITOR" = true ]; then
    step "3/3  Editor (Vite dev server)"
    echo ""
    echo -e "  ${DIM}The visual workflow editor lets you drag-and-drop FSM states,${NC}"
    echo -e "  ${DIM}edit system prompts, and rewire transitions — all in your browser.${NC}"
    echo -e "  ${DIM}This Vite dev server provides hot-reload during development.${NC}"
    echo -e "  ${DIM}In production, the editor is pre-built and served by the backend.${NC}"
    echo ""

    echo "  Starting Vite dev server..."
    cd "$EDITOR_DIR"
    npx vite --port 5174 &
    EDITOR_PID=$!
    cd "$PROJECT_ROOT"

    # Quick wait for Vite
    sleep 2
    if kill -0 "$EDITOR_PID" 2>/dev/null; then
        ok "Editor dev server on :5174"
        EDITOR_STATUS="${GREEN}running${NC}"
        EDITOR_URL="http://localhost:5174"
    else
        warn "Editor failed to start"
        EDITOR_STATUS="${YELLOW}failed${NC}"
        EDITOR_PID=""
    fi
else
    if [ "$SKIP_EDITOR" = true ]; then
        EDITOR_STATUS="${DIM}skipped (--no-editor)${NC}"
    fi
fi

# ── Dashboard ────────────────────────────────────────────────
echo ""
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  All components started — your app is ready to use${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Component          Status          URL${NC}"
echo -e "  ─────────────────────────────────────────────────────"
printf "  %-19s" "RAG"
echo -e "${RAG_STATUS}$([ -n "$RAG_URL" ] && echo -e "       ${CYAN}$RAG_URL${NC}")"
printf "  %-19s" "Backend"
echo -e "${BACKEND_STATUS}$([ -n "$BACKEND_URL" ] && echo -e "       ${CYAN}$BACKEND_URL${NC}")"
printf "  %-19s" "Editor"
echo -e "${EDITOR_STATUS}$([ -n "$EDITOR_URL" ] && echo -e "       ${CYAN}$EDITOR_URL${NC}")"
echo ""
echo -e "  ${BOLD}Quick links:${NC}"
echo -e "    Browser client:  ${CYAN}http://localhost:$PORT${NC}"
echo -e "    Admin panel:     ${CYAN}http://localhost:$PORT/admin${NC}"
echo -e "    FSM workflow:    ${CYAN}http://localhost:$PORT/fsm${NC}"
echo -e "    Health check:    ${CYAN}http://localhost:$PORT/health${NC}"
if [ -n "$EDITOR_URL" ]; then
echo -e "    Editor (dev):    ${CYAN}$EDITOR_URL${NC}"
fi
echo ""
echo -e "  ${DIM}Ctrl+C to stop all services${NC}"
echo ""

# ── Wait for processes ───────────────────────────────────────
# Keep running until a background process dies or user hits Ctrl+C
wait $BACKEND_PID 2>/dev/null
cleanup
