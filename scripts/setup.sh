#!/bin/bash
# Setup script for voice-calendar-scheduler-FSM
#
# Creates a venv, installs all dependencies, sets up git hooks,
# and verifies everything works.
#
# Usage:
#   ./scripts/setup.sh          # Full setup
#   ./scripts/setup.sh --quick  # Skip voice packages (faster-whisper, piper-tts)

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="$PROJECT_ROOT/.venv"
QUICK=false
UPDATE_DEPS=false

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --update-deps) UPDATE_DEPS=true ;;
    esac
done

# ── Colors ────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

# ── Step 1: Find Python ──────────────────────────────────────
echo ""
echo "═══ Voice Calendar Scheduler FSM — Setup ═══"
echo ""

# Prefer python3.13, fall back to python3.12, then python3
PYTHON=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.11+ required. Found: $(python3 --version 2>&1 || echo 'none')"
    exit 1
fi
ok "Python: $($PYTHON --version)"

# ── Step 2: Git submodules ───────────────────────────────────
echo ""
echo "── Git submodules ──"

if [ ! -f "$PROJECT_ROOT/engine-repo/engine/orchestrator.py" ]; then
    echo "  Cloning engine submodule..."
    git submodule update --init --recursive
fi
ok "Engine submodule: engine-repo/"

# Ensure symlink exists
if [ ! -L "$PROJECT_ROOT/engine" ]; then
    ln -sf engine-repo/engine "$PROJECT_ROOT/engine"
fi
ok "Symlink: engine/ → engine-repo/engine/"

# ── Step 3: Create venv ─────────────────────────────────────
echo ""
echo "── Virtual environment ──"

if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating venv with $PYTHON..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created: .venv/"
else
    ok "Exists: .venv/"
fi

PIP="$VENV_DIR/bin/pip"
PY="$VENV_DIR/bin/python"

# Upgrade pip quietly
"$PIP" install --upgrade pip -q 2>/dev/null

# ── Step 4: Install dependencies ─────────────────────────────
echo ""
echo "── Dependencies ──"

# Use lock file if available and --update-deps not specified
LOCK_FILE="$PROJECT_ROOT/requirements-lock.txt"
if [ -f "$LOCK_FILE" ] && [ "$UPDATE_DEPS" = false ]; then
    echo "  Installing from lock file (use --update-deps to re-resolve)..."
    "$PIP" install -q -r "$LOCK_FILE" 2>/dev/null && ok "Installed from requirements-lock.txt" \
        || warn "Lock file install had issues — falling back to range install"
fi

# Core packages (always needed)
echo "  Installing core packages..."
"$PIP" install -q \
    'fastapi>=0.110,<1' \
    'uvicorn[standard]>=0.27,<1' \
    'aiohttp>=3.9,<4' \
    'python-dotenv>=1.0' \
    'pydantic>=2.0,<3' \
    'pydantic-settings>=2.0,<3' \
    'httpx>=0.27,<1' \
    'numpy>=1.24,<2' \
    'audioop-lts>=0.2'
ok "Core: fastapi, uvicorn, pydantic, httpx, numpy"

# LLM providers
echo "  Installing LLM packages..."
"$PIP" install -q \
    'anthropic>=0.40,<1' \
    'ollama>=0.4,<1'
ok "LLM: anthropic, ollama"

# Calendar & Twilio
echo "  Installing integration packages..."
"$PIP" install -q \
    'google-api-python-client>=2.100,<3' \
    'google-auth>=2.20,<3' \
    'twilio>=9.0,<10'
ok "Integrations: google-calendar, twilio"

# Testing
echo "  Installing test packages..."
"$PIP" install -q \
    'pytest>=8.0,<9' \
    'pytest-asyncio>=0.23,<1'
ok "Testing: pytest, pytest-asyncio"

# Voice packages (heavy — skip with --quick)
if [ "$QUICK" = true ]; then
    warn "Skipping voice packages (--quick). Install later with:"
    warn "  .venv/bin/pip install faster-whisper piper-tts aiortc av kokoro-onnx"
else
    echo "  Installing voice packages (this may take a minute)..."
    "$PIP" install -q \
        'faster-whisper>=1.0,<2' \
        'piper-tts>=1.2,<2' \
        'aiortc>=1.9,<2' \
        'av>=12.0' \
        'scipy>=1.10' \
        2>/dev/null && ok "Voice: faster-whisper, piper-tts, aiortc, scipy" \
        || warn "Some voice packages failed to install (may need system deps)"

    # Kokoro TTS (high-quality multi-voice engine)
    echo "  Installing Kokoro TTS..."
    "$PIP" install -q 'kokoro-onnx>=0.5.0' \
        2>/dev/null && ok "Kokoro: kokoro-onnx (voice admin at /admin)" \
        || warn "kokoro-onnx failed to install (optional — admin voice selection)"
fi

# ── Step 5: Environment file ────────────────────────────────
echo ""
echo "── Configuration ──"

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    ok "Created .env from .env.example"
    warn "Edit .env to add your API keys"
else
    ok ".env already exists"
fi

# ── Step 6: Git hooks ───────────────────────────────────────
echo ""
echo "── Git hooks ──"

if [ -d "$PROJECT_ROOT/.git" ]; then
    "$PROJECT_ROOT/scripts/setup-hooks.sh" 2>/dev/null
    ok "Pre-commit hook installed"
else
    warn "Not a git repo — skipping hooks"
fi

# ── Step 7: Build project memory index ──────────────────────
echo ""
echo "── Project memory ──"

if command -v jq &>/dev/null; then
    "$PROJECT_ROOT/scripts/build-index.sh" 2>/dev/null
    ok "Project memory index built"
else
    warn "jq not found — skipping index build (brew install jq)"
fi

# ── Step 8: Verify ──────────────────────────────────────────
echo ""
echo "── Verification ──"

# Test critical imports
"$PY" -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'engine-repo')
from scheduling.config import settings
from scheduling.app import app
from scheduling.session import SchedulingSession
from scheduling.workflows.apartment_viewing import STEPS, STEP_ORDER
print(f'  App: {app.title}')
print(f'  FSM: {len(STEPS)} steps — {\" → \".join(STEP_ORDER)}')
print(f'  Config: {settings.host}:{settings.port}')
" 2>&1 && ok "All imports OK" || fail "Import check failed"

# Run tests
echo ""
echo "  Running tests..."
PYTHONPATH=".:engine-repo" "$PY" -m pytest tests/ -q 2>&1 | tail -1

# ── Step 9: Voice stack ────────────────────────────────────
echo ""
echo "── Voice stack ──"

SETUP_VOICE=false
if [ "$QUICK" = true ]; then
    warn "Skipping voice setup (--quick)"
else
    read -r -p "  Set up voice components (STT, TTS, LLM)? [Y/n]: " VOICE_REPLY
    VOICE_REPLY="${VOICE_REPLY:-Y}"
    case "$VOICE_REPLY" in
        [Yy]*) SETUP_VOICE=true ;;
    esac
fi

if [ "$SETUP_VOICE" = true ]; then
    "$PROJECT_ROOT/scripts/setup-voice.sh"
else
    echo ""
    echo "  Run voice setup later with:"
    echo "    ./scripts/setup-voice.sh"
fi

# ── Done ────────────────────────────────────────────────────
echo ""
echo "═══ Setup complete! ═══"
echo ""
echo "  Start the server:"
echo "    ./scripts/run.sh"
echo ""
echo "  Or manually:"
echo "    PYTHONPATH=\".:engine-repo\" .venv/bin/uvicorn scheduling.app:app --port 8090"
echo ""
echo "  Open in browser:"
echo "    http://localhost:8090"
echo "    http://localhost:8090/admin  (voice & barge-in settings)"
echo ""
