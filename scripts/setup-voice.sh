#!/bin/bash
# Interactive voice stack configurator
#
# Walks through setting up each component needed for the full voice loop:
#   1. LLM provider (Claude or Ollama)
#   2. Speech-to-Text (faster-whisper + model download)
#   3. Text-to-Speech (piper-tts + voice download)
#   4. WebRTC audio (aiortc + av)
#
# Each step can be skipped individually.
#
# Usage:
#   ./scripts/setup-voice.sh          # Interactive mode
#   ./scripts/setup-voice.sh --auto   # Non-interactive (accept defaults)

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="$PROJECT_ROOT/.venv"
PIP="$VENV_DIR/bin/pip"
PY="$VENV_DIR/bin/python"
ENV_FILE="$PROJECT_ROOT/.env"

AUTO=false
if [ "$1" = "--auto" ]; then
    AUTO=true
fi

# ── Colors & helpers ─────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
step() { echo -e "\n${BOLD}── $1 ──${NC}"; }

ask() {
    # ask "prompt" "default" → sets REPLY
    local prompt="$1"
    local default="$2"
    if [ "$AUTO" = true ]; then
        REPLY="$default"
        return
    fi
    if [ -n "$default" ]; then
        read -r -p "  $prompt [$default]: " REPLY
        REPLY="${REPLY:-$default}"
    else
        read -r -p "  $prompt: " REPLY
    fi
}

ask_yn() {
    # ask_yn "prompt" "Y" → returns 0 for yes, 1 for no
    local prompt="$1"
    local default="${2:-Y}"
    if [ "$AUTO" = true ]; then
        [ "$default" = "Y" ] && return 0 || return 1
    fi
    local hint="Y/n"
    [ "$default" = "N" ] && hint="y/N"
    read -r -p "  $prompt [$hint]: " REPLY
    REPLY="${REPLY:-$default}"
    case "$REPLY" in
        [Yy]*) return 0 ;;
        *) return 1 ;;
    esac
}

# ── Prechecks ────────────────────────────────────────────────

if [ ! -d "$VENV_DIR" ]; then
    fail "Virtual environment not found at .venv/"
    echo "  Run ./scripts/setup.sh first."
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$PROJECT_ROOT/.env.example" ]; then
        cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
        warn "Created .env from .env.example"
    else
        fail "No .env file found"
        exit 1
    fi
fi

echo ""
echo -e "${BOLD}═══ Voice Stack Setup ═══${NC}"

# ── Step 1: LLM Provider ────────────────────────────────────

step "Step 1: LLM Provider"

# Read current config
CURRENT_PROVIDER=$(grep -E '^LLM_PROVIDER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
CURRENT_KEY=$(grep -E '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')

echo "  Which LLM provider?"
echo "    1) Claude (Anthropic API) — recommended, fast"
echo "    2) Ollama (local, free) — requires ~4GB RAM"
echo "    3) Skip for now"

if [ "$AUTO" = true ]; then
    LLM_CHOICE="1"
else
    read -r -p "  > " LLM_CHOICE
    LLM_CHOICE="${LLM_CHOICE:-1}"
fi

case "$LLM_CHOICE" in
    1)
        # Claude
        if [ -n "$CURRENT_KEY" ] && [ "$CURRENT_KEY" != "sk-ant-..." ]; then
            echo "  Found existing API key: ${CURRENT_KEY:0:12}..."
            if ask_yn "Use this key?" "Y"; then
                API_KEY="$CURRENT_KEY"
            else
                ask "Enter your Anthropic API key (sk-ant-...)" ""
                API_KEY="$REPLY"
            fi
        else
            ask "Enter your Anthropic API key (sk-ant-...)" ""
            API_KEY="$REPLY"
        fi

        if [ -z "$API_KEY" ]; then
            warn "No API key provided — skipping LLM setup"
        else
            # Update .env
            if grep -q '^ANTHROPIC_API_KEY=' "$ENV_FILE"; then
                sed -i.bak "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$API_KEY|" "$ENV_FILE"
            else
                echo "ANTHROPIC_API_KEY=$API_KEY" >> "$ENV_FILE"
            fi
            if grep -q '^LLM_PROVIDER=' "$ENV_FILE"; then
                sed -i.bak "s|^LLM_PROVIDER=.*|LLM_PROVIDER=claude|" "$ENV_FILE"
            else
                echo "LLM_PROVIDER=claude" >> "$ENV_FILE"
            fi
            rm -f "$ENV_FILE.bak"

            # Validate with a test API call
            echo "  Validating API key..."
            VALIDATE_RESULT=$("$PY" -c "
import sys, os
sys.path.insert(0, '.')
sys.path.insert(0, 'engine-repo')
os.environ['ANTHROPIC_API_KEY'] = '$API_KEY'
try:
    import anthropic
    client = anthropic.Anthropic(api_key='$API_KEY')
    resp = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=10,
        messages=[{'role': 'user', 'content': 'Say OK'}],
    )
    print('OK:' + resp.model)
except Exception as e:
    print('ERR:' + str(e))
" 2>&1)

            if [[ "$VALIDATE_RESULT" == OK:* ]]; then
                MODEL_USED="${VALIDATE_RESULT#OK:}"
                ok "Claude API key verified (model: $MODEL_USED)"
            else
                ERROR_MSG="${VALIDATE_RESULT#ERR:}"
                warn "API key validation failed: $ERROR_MSG"
                warn "Key saved to .env — you can fix it later"
            fi
        fi
        ;;

    2)
        # Ollama
        if grep -q '^LLM_PROVIDER=' "$ENV_FILE"; then
            sed -i.bak "s|^LLM_PROVIDER=.*|LLM_PROVIDER=ollama|" "$ENV_FILE"
        else
            echo "LLM_PROVIDER=ollama" >> "$ENV_FILE"
        fi
        rm -f "$ENV_FILE.bak"

        # Check if Ollama is running
        if command -v ollama &>/dev/null; then
            ok "Ollama CLI found"
            if curl -s http://localhost:11434/api/tags &>/dev/null; then
                ok "Ollama is running"

                # Check for models
                MODELS=$(curl -s http://localhost:11434/api/tags | "$PY" -c "
import sys, json
data = json.load(sys.stdin)
models = [m['name'] for m in data.get('models', [])]
print(','.join(models) if models else '')
" 2>/dev/null)

                if [ -n "$MODELS" ]; then
                    ok "Installed models: $MODELS"
                else
                    warn "No models installed"
                    OLLAMA_MODEL=$(grep -E '^OLLAMA_MODEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
                    OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
                    if ask_yn "Pull $OLLAMA_MODEL now? (~4GB download)" "Y"; then
                        echo "  Pulling $OLLAMA_MODEL (this may take a while)..."
                        ollama pull "$OLLAMA_MODEL" && ok "Model pulled: $OLLAMA_MODEL" \
                            || warn "Model pull failed — run 'ollama pull $OLLAMA_MODEL' manually"
                    fi
                fi
            else
                warn "Ollama not running — start with: ollama serve"
            fi
        else
            warn "Ollama not installed — see: https://ollama.ai"
        fi
        ;;

    3)
        warn "Skipping LLM setup"
        ;;
esac

# ── Step 2: Speech-to-Text ──────────────────────────────────

step "Step 2: Speech-to-Text"

if ask_yn "Install faster-whisper for speech recognition?" "Y"; then
    echo "  Installing faster-whisper + scipy..."
    "$PIP" install -q 'faster-whisper>=1.0,<2' 'scipy>=1.10' 2>&1 | grep -i error || true
    if "$PY" -c "import faster_whisper" 2>/dev/null; then
        ok "faster-whisper installed"
    else
        fail "faster-whisper install failed"
        warn "Try: .venv/bin/pip install faster-whisper scipy"
    fi

    echo "  Downloading Whisper 'base' model (~75MB)..."
    DOWNLOAD_RESULT=$("$PY" -c "
import sys
sys.path.insert(0, 'engine-repo')
try:
    from faster_whisper import WhisperModel
    model = WhisperModel('base', compute_type='int8', device='cpu')
    print('OK')
except Exception as e:
    print('ERR:' + str(e))
" 2>&1)

    if [[ "$DOWNLOAD_RESULT" == *"OK"* ]]; then
        ok "STT model ready (base, int8, CPU)"
    else
        ERROR_MSG="${DOWNLOAD_RESULT#ERR:}"
        warn "Model download issue: $ERROR_MSG"
    fi
else
    warn "Skipping STT setup"
fi

# ── Step 3: Text-to-Speech ──────────────────────────────────

step "Step 3: Text-to-Speech"

if ask_yn "Install piper-tts for speech synthesis?" "Y"; then
    echo "  Installing piper-tts..."
    "$PIP" install -q 'piper-tts>=1.2,<2' 2>&1 | grep -i error || true
    if "$PY" -c "import piper" 2>/dev/null; then
        ok "piper-tts installed"
    else
        fail "piper-tts install failed"
        warn "Try: .venv/bin/pip install piper-tts"
    fi

    echo "  Downloading voice: en_US-lessac-medium (~35MB)..."
    DOWNLOAD_RESULT=$("$PY" -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'engine-repo')
try:
    from engine.tts import synthesize
    audio = synthesize('hello')
    if audio and len(audio) > 0:
        print('OK')
    else:
        print('ERR:no audio output')
except Exception as e:
    print('ERR:' + str(e))
" 2>&1)

    if [[ "$DOWNLOAD_RESULT" == *"OK"* ]]; then
        ok "TTS voice ready (en_US-lessac-medium)"
    else
        ERROR_MSG="${DOWNLOAD_RESULT#ERR:}"
        warn "Voice download issue: $ERROR_MSG"
    fi
else
    warn "Skipping TTS setup"
fi

# ── Step 4: WebRTC (browser audio) ──────────────────────────

step "Step 4: WebRTC (browser audio)"

if ask_yn "Install aiortc for WebRTC browser audio?" "Y"; then
    echo "  Installing aiortc + av..."
    "$PIP" install -q 'aiortc>=1.9,<2' 'av>=12.0' 2>&1 | grep -i error || true
    if "$PY" -c "import aiortc" 2>/dev/null; then
        ok "aiortc + av installed"
    else
        fail "aiortc install failed"
        warn "Try: .venv/bin/pip install aiortc av"
    fi
else
    warn "Skipping WebRTC setup"
fi

# ── Verification ─────────────────────────────────────────────

step "Verification"

echo "  Running smoke tests..."

# Test STT
STT_RESULT=$("$PY" -c "
import sys, time
sys.path.insert(0, '.')
sys.path.insert(0, 'engine-repo')
try:
    t0 = time.time()
    from engine.stt import transcribe
    dt = time.time() - t0
    print(f'OK:{dt:.1f}s')
except ImportError as e:
    print(f'SKIP:{e}')
except Exception as e:
    print(f'ERR:{e}')
" 2>&1)

case "$STT_RESULT" in
    OK:*) ok "STT: loaded model in ${STT_RESULT#OK:}" ;;
    SKIP:*) warn "STT: ${STT_RESULT#SKIP:}" ;;
    *) warn "STT: ${STT_RESULT#ERR:}" ;;
esac

# Test TTS
TTS_RESULT=$("$PY" -c "
import sys, time
sys.path.insert(0, '.')
sys.path.insert(0, 'engine-repo')
try:
    from engine.tts import synthesize
    t0 = time.time()
    audio = synthesize('hello')
    dt = time.time() - t0
    if audio and len(audio) > 0:
        print(f'OK:{dt:.1f}s')
    else:
        print('ERR:no audio')
except ImportError as e:
    print(f'SKIP:{e}')
except Exception as e:
    print(f'ERR:{e}')
" 2>&1)

case "$TTS_RESULT" in
    OK:*) ok "TTS: synthesized \"hello\" in ${TTS_RESULT#OK:}" ;;
    SKIP:*) warn "TTS: ${TTS_RESULT#SKIP:}" ;;
    *) warn "TTS: ${TTS_RESULT#ERR:}" ;;
esac

# Test LLM
LLM_RESULT=$("$PY" -c "
import sys, time
sys.path.insert(0, '.')
sys.path.insert(0, 'engine-repo')
from dotenv import load_dotenv
load_dotenv('.env')
try:
    from engine.llm import is_configured, get_provider_name
    if is_configured():
        provider = get_provider_name()
        print(f'OK:{provider}')
    else:
        print('SKIP:no provider configured')
except ImportError:
    print('SKIP:not installed')
except Exception as e:
    print(f'ERR:{e}')
" 2>&1)

case "$LLM_RESULT" in
    OK:*) ok "LLM: ${LLM_RESULT#OK:} configured" ;;
    SKIP:*) warn "LLM: ${LLM_RESULT#SKIP:}" ;;
    *) warn "LLM: ${LLM_RESULT#ERR:}" ;;
esac

# Test WebRTC
WEBRTC_RESULT=$("$PY" -c "
try:
    import aiortc
    print('OK')
except ImportError:
    print('SKIP:not installed')
except Exception as e:
    print(f'ERR:{e}')
" 2>&1)

case "$WEBRTC_RESULT" in
    OK*) ok "WebRTC: aiortc available" ;;
    SKIP:*) warn "WebRTC: not installed" ;;
    *) warn "WebRTC: ${WEBRTC_RESULT#ERR:}" ;;
esac

# ── Done ─────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══ Voice stack setup complete! ═══${NC}"
echo ""
echo "  Start the server:"
echo "    ./scripts/run.sh"
echo ""
echo "  Open in browser:"
echo "    http://localhost:8090"
echo ""
echo "  Re-run this script anytime:"
echo "    ./scripts/setup-voice.sh"
echo ""
