# Voice Calendar Scheduler FSM

A 24/7 voice-driven apartment viewing scheduling assistant. Callers dial a Twilio phone number or connect via browser WebRTC, describe what apartment they're looking for, and the system searches listings, checks Google Calendar availability, and books a viewing appointment — all through natural voice conversation guided by an 8-step Finite State Machine.

## Architecture

```
Caller (phone/browser)
    │
    ├── Twilio PSTN ──→ Media Streams WS ──→ TwilioMediaStreamChannel (mulaw 8kHz ↔ PCM 16kHz)
    │                                              │
    └── Browser WebRTC ──→ Signaling WS ──→ WebRTCChannel (Opus 48kHz ↔ PCM 16kHz)
                                                   │
                                              SchedulingSession
                                                   │
                                    ┌──────────────┼──────────────┐
                                    │              │              │
                                STT (whisper)   FSM (8 steps)  TTS (piper)
                                                   │
                                    ┌──────────────┼──────────────┐
                                    │              │              │
                              RAG search    Google Calendar   LLM (Claude/Ollama)
```

**Key components:**

- **Engine** (`engine-repo/`): Git submodule providing the FSM orchestrator, LLM abstraction, STT (faster-whisper), TTS (Piper), and tool framework
- **Scheduling** (`scheduling/`): Domain application — session management, voice channels, calendar integration, workflow FSM, and tools
- **Gateway** (`gateway/`): WebRTC signaling, TURN credential fetching, browser client support
- **RAG Service**: Apartment listings search via LanceDB + nomic-embed (runs in Docker)

## 8-Step Scheduling Workflow

| Step | Type | What Happens |
|------|------|-------------|
| 1. Greet & Gather | LLM | Extract preferences: bedrooms, budget, area, move-in date |
| 2. Search Listings | Tool | Query RAG service for matching apartments |
| 3. Present Options | LLM | Narrate top 2-3 matches to caller |
| 4. Check Availability | Tool | Query Google Calendar freeBusy API for open slots |
| 5. Propose Times | LLM | Present 2-3 available viewing slots |
| 6. Collect Details | LLM | Gather caller name, email, confirm slot |
| 7. Create Booking | Tool | Create Google Calendar event, send invite |
| 8. Confirm & Done | LLM | Confirm details, say goodbye |

## Setup

### Prerequisites

- Python 3.11+ (3.13 recommended)
- Git (for submodules)
- Docker (optional, for RAG apartment search)
- Node.js (optional, for the visual workflow editor)

### Quick Start

```bash
# Clone with submodules
git clone --recursive <repo-url>
cd voice-calendar-scheduler-FSM

# Run setup (creates venv, installs deps, runs tests)
./scripts/setup.sh

# Configure — copy the example and add your API keys
cp .env.example .env
$EDITOR .env

# Start everything (RAG + Backend + Editor)
./scripts/start.sh
```

The setup script:
- Finds Python 3.11+ on your system
- Initializes the engine git submodule
- Creates a virtual environment in `.venv/`
- Installs all dependencies (core, LLM, integrations, voice)
- Creates `.env` from `.env.example`
- Installs git hooks and builds the project memory index
- Runs verification and tests

Use `./scripts/setup.sh --quick` to skip heavy voice packages (faster-whisper, piper-tts, aiortc) for faster setup during development.

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `LLM_PROVIDER` | Yes | `claude` or `ollama` |
| `ANTHROPIC_API_KEY` | If claude | Claude API key |
| `OLLAMA_MODEL` | If ollama | Model name (e.g. `qwen2.5:7b`) |
| `ADMIN_API_KEY` | Recommended | Secures admin/debug endpoints (see [Security](#security)) |
| `TWILIO_ACCOUNT_SID` | For phone | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | For phone | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | For phone | Your Twilio number |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | For booking | Path to service account JSON |
| `GOOGLE_CALENDAR_ID` | For booking | Calendar ID (default: `primary`) |
| `RAG_SERVICE_URL` | For search | RAG endpoint (default: `http://localhost:8000`) |
| `HOST` | No | Bind address (default: `127.0.0.1`) |
| `PORT` | No | Server port (default: `8090`) |
| `DEBUG` | No | Debug mode — relaxes auth for local dev (default: `false`) |
| `ICE_SERVERS_JSON` | No | Fallback ICE servers for WebRTC |

## Running

### Recommended: `start.sh` (starts everything)

The easiest way to run the app. This single script validates your config, starts all three components in order, waits for each to become healthy, and shows a status dashboard.

```bash
# Start everything — RAG, Backend, and Editor
./scripts/start.sh

# Skip the RAG Docker container (apartment search won't work)
./scripts/start.sh --no-rag

# Skip the editor dev server
./scripts/start.sh --no-editor

# Just validate config without starting anything
./scripts/start.sh --check
```

The script pauses between each component so you can see what's happening. Press Enter to continue, or Ctrl+C at any time to stop all services.

### Manual startup

If you prefer to start components individually:

```bash
# Backend only
PYTHONPATH=".:engine-repo" .venv/bin/uvicorn scheduling.app:app --host 127.0.0.1 --port 8090 --reload

# RAG service (Docker)
docker compose up -d rag

# Editor dev server
cd web/editor && npx vite --port 5174
```

### Once running

| URL | What |
|-----|------|
| http://localhost:8090 | Browser client — make a call from your browser |
| http://localhost:8090/admin | Admin panel — voice selection, barge-in, runtime config |
| http://localhost:8090/fsm | FSM viewer — session list, step inspector, debug stream |
| http://localhost:8090/health | Health check — component status (RAG, STT, TTS, LLM) |
| http://localhost:5174 | Visual workflow editor (dev only, if editor is running) |

For Twilio phone calls, configure your Twilio number webhook to POST to `https://<your-host>/twilio/voice`.

### RAG Service (Apartment Search)

The RAG service powers the apartment search tool. It runs as a Docker container with LanceDB + nomic-embed for semantic search. **Data persists across restarts** via a Docker volume — you only need to ingest once.

#### First-time setup

```bash
# 1. Start the RAG service (first boot takes ~30s to load the embedding model)
docker compose up -d rag

# 2. Wait for it to be healthy
curl -sf http://localhost:8000/health
# → {"status":"healthy","documents":0,...}

# 3. Ingest apartment listings (choose one)

# Option A: Sample data (10 hand-crafted Austin listings, instant)
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.ingest

# Option B: Kaggle data (535 real Austin TX listings, ~30s)
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.ingest --data listings/data/austin_apartments.json

# 4. Verify search works
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "2 bedroom pet friendly", "top_k": 3}' | python3 -m json.tool
```

#### After a restart

Data persists in the Docker volume, so just start the container:

```bash
docker compose up -d rag
# Wait ~30s for model load, then it's ready with all previously ingested data
curl -sf http://localhost:8000/health
# → {"status":"healthy","documents":535,...}
```

You do **not** need to re-ingest. The only time you need to ingest again is if you:
- Remove the Docker volume (`docker volume rm voice-calendar-scheduler-fsm_rag-data`)
- Want to add new/different listings

#### Importing your own CSV data (optional)

To import from a different apartment CSV (e.g., a fresh Kaggle download):

```bash
# Download from https://www.kaggle.com/datasets/shashanks1202/apartment-rent-data

# Import and filter to Austin TX
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.import_csv \
    /path/to/apartments_for_rent_classified_100K.csv \
    --city Austin --state TX \
    --output listings/data/austin_apartments.json

# Then ingest into RAG
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.ingest --data listings/data/austin_apartments.json
```

The import pipeline auto-detects CSV delimiters (comma, semicolon, tab) and uses a configurable column mapping (`listings/data/column_mappings/kaggle_shashanks1202.json`). To use a different CSV format, create a new mapping file and pass `--mapping-file`.

### Google Calendar (Appointment Booking)

The calendar integration lets the system check real availability and book viewing appointments on your behalf. It uses a **Google Service Account** — a bot identity that doesn't require user login. Without it, the app still works but the booking steps are disabled.

#### Step 1: Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Google Calendar API**:
   - Navigate to **APIs & Services → Library**
   - Search for "Google Calendar API" and click **Enable**

#### Step 2: Create a service account

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → Service Account**
3. Give it a name (e.g., `calendar-scheduler`) and click **Done**
4. Click into the new service account, go to the **Keys** tab
5. Click **Add Key → Create new key → JSON**
6. Save the downloaded JSON file somewhere safe (e.g., `~/.config/calendar-service-account.json`)

#### Step 3: Share your calendar with the service account

The service account has its own identity (an email like `calendar-scheduler@your-project.iam.gserviceaccount.com`). It can't see your calendar until you share it:

1. Open [Google Calendar](https://calendar.google.com/)
2. Find the calendar you want to use in the left sidebar
3. Click the three dots → **Settings and sharing**
4. Under **Share with specific people**, click **Add people**
5. Paste the service account email (from the JSON file's `client_email` field)
6. Set permission to **Make changes to events**
7. Click **Send**

#### Step 4: Configure `.env`

```bash
# Path to the JSON key file you downloaded in Step 2
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/calendar-service-account.json

# Calendar ID — use "primary" for the service account's own calendar,
# or the specific calendar ID from Google Calendar settings
# (looks like: abc123@group.calendar.google.com)
GOOGLE_CALENDAR_ID=primary
```

The app uses the `https://www.googleapis.com/auth/calendar` scope for full read/write access (checking availability via freeBusy, creating events with attendee invitations, and cancelling bookings).

**If not configured**, the startup script shows a warning and the app runs without calendar tools — the FSM conversation still works but skips the availability-check and booking steps.

### cal-provider Library (Standalone Calendar Package)

The calendar integration layer has been extracted into a standalone Python library at `../cal-provider/`. This means you can reuse the same calendar backends (Google, CalDAV) from other projects or expose them as an MCP server for AI agents.

**This project already uses it** — `scheduling/calendar_providers/` is a thin re-export shim that delegates to `cal-provider`. No configuration changes needed for the FSM.

#### Using cal-provider in other projects

```bash
# Install from local path (pick the backends you need)
pip install -e "../cal-provider[google]"   # Google Calendar only
pip install -e "../cal-provider[caldav]"   # CalDAV only (iCloud, Nextcloud, Fastmail)
pip install -e "../cal-provider[all]"      # Everything + MCP server
```

```python
from cal_provider import get_provider

# Google Calendar
provider = get_provider("google", service_account_path="/path/to/sa.json")

# CalDAV (iCloud)
provider = get_provider("caldav",
    url="https://caldav.icloud.com/",
    username="you@icloud.com",
    password="app-specific-password",
)

# Same API regardless of backend
calendars = await provider.list_calendars()
slots = await provider.get_available_slots("primary", start, end, duration_minutes=30)
events = await provider.get_events("primary", start, end)
result = await provider.create_event("primary", event)
await provider.cancel_event("primary", event_id)
```

#### CalDAV setup (iCloud, Nextcloud, Fastmail)

| Provider | URL | Auth |
|----------|-----|------|
| iCloud | `https://caldav.icloud.com/` | Apple ID + [app-specific password](https://appleid.apple.com/) |
| Nextcloud | `https://your-server/remote.php/dav/` | Account username/password |
| Fastmail | `https://caldav.fastmail.com/dav/calendars/` | Account username + app password |

#### MCP server (for AI agents)

Expose calendar tools to Claude, Cursor, or any MCP-compatible client:

```bash
# Set backend
export CAL_PROVIDER=google
export GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/sa.json

# Or CalDAV
export CAL_PROVIDER=caldav
export CALDAV_URL=https://caldav.icloud.com/
export CALDAV_USERNAME=you@icloud.com
export CALDAV_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Start
cd ../cal-provider
.venv/bin/cal-provider-mcp
```

To add to Claude Code, put this in `.claude/settings.json`:

```json
{
  "mcpServers": {
    "calendar": {
      "command": "/path/to/cal-provider/.venv/bin/cal-provider-mcp",
      "env": {
        "CAL_PROVIDER": "google",
        "GOOGLE_SERVICE_ACCOUNT_JSON": "/path/to/sa.json"
      }
    }
  }
}
```

This gives the AI 6 tools: `list_calendars`, `get_available_slots`, `get_events`, `create_event`, `update_event`, `cancel_event`.

#### Running cal-provider tests

```bash
cd ../cal-provider
.venv/bin/python -m pytest tests/ -v   # 40 tests, all mocked (no credentials needed)
```

## Security

Admin and debug endpoints are protected by bearer token authentication. Set `ADMIN_API_KEY` in your `.env` to enable it:

```bash
# Generate a secure token
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Add to .env
ADMIN_API_KEY=your-generated-token
```

**How it works:**

| Scenario | Behavior |
|----------|----------|
| `ADMIN_API_KEY` set + valid token | Access granted |
| `ADMIN_API_KEY` set + wrong/missing token | 401 Unauthorized |
| `ADMIN_API_KEY` empty + `DEBUG=true` | Access granted (local dev convenience) |
| `ADMIN_API_KEY` empty + `DEBUG=false` | 403 Forbidden (locked in production) |

**Protected endpoints:** `POST /api/config`, `POST /api/tts/preview`, `PATCH /api/fsm/steps/{id}`, all session endpoints (`/api/fsm/sessions/*`), workflow mutation endpoints, and the WebSocket debug stream.

**Public endpoints** (no auth required): `/health`, `GET /api/config`, `GET /api/fsm/steps`, `GET /api/workflow/{id}`, `GET /api/voices`, static HTML pages, Twilio/WebRTC call endpoints.

The frontend (admin panel, FSM viewer, editor) prompts for the token on first use and stores it in `localStorage`.

### Additional hardening

- **Default bind address**: `127.0.0.1` (not `0.0.0.0`) — won't be internet-exposed unless you change it
- **Session IDs**: 144-bit cryptographic tokens (`secrets.token_urlsafe`) instead of truncated UUIDs
- **Input validation**: Workflow/state IDs are regex-validated to prevent path traversal
- **State PATCH allowlist**: Only known safe fields can be modified via the API (prevents Pydantic internal injection)
- **PII redaction**: Phone numbers and caller details are masked in log output; full utterances logged only at DEBUG level

## Admin Panel

The admin panel at `/admin` provides runtime controls:

- **Barge-in detection** — Toggle whether users can interrupt the bot mid-speech
- **Voice selection** — Switch between Kokoro and Piper TTS engines, pick from ~40 Kokoro voices across 6 languages
- **Live config** — View current runtime settings as JSON

To use Kokoro voices, install `kokoro-onnx` and ensure the model files are present:

```bash
.venv/bin/pip install kokoro-onnx
# Model files: ../kokoro-tts/kokoro-v1.0.onnx and ../kokoro-tts/voices-v1.0.bin
```

## Testing

### Run All Tests

```bash
PYTHONPATH=".:engine-repo" .venv/bin/python -m pytest tests/ -v
```

### Test Suites

#### Debug Tracing Tests (33 tests)

Tests that the debug event system works end-to-end: broadcaster pub/sub, session event wiring, field progress detection, and no PROGRESS leak in system prompts.

```bash
PYTHONPATH=".:engine-repo" .venv/bin/python -m pytest tests/test_debug_tracing.py -v
```

![Debug tracing tests — 33 passed](screenshots/test-debug-tracing-33-passed.png)

#### E2E Voice Harness Tests (20 tests)

Full voice pipeline tests using real Piper TTS + Faster-Whisper STT with mocked LLM. Verifies TTS/STT round-trip fidelity, greeting flow, utterance handling, debug event emission, FSM state transitions, and response quality (no null, no PROGRESS, no raw JSON).

```bash
PYTHONPATH=".:engine-repo" .venv/bin/python -m pytest tests/test_e2e_voice_harness.py -v
```

![E2E voice harness tests — 20 passed](screenshots/test-e2e-voice-harness-20-passed.png)

The E2E harness uses two distinct Piper voices:
- **System voice**: `en_US-lessac-medium` (the assistant)
- **Caller voice**: `en_US-hfc_female-medium` (simulated caller)

TTS/STT tests gracefully skip if voice models aren't downloaded. Session-only tests (with mocked LLM) always run.

#### Other Test Suites

| Suite | Tests | Coverage |
|-------|-------|----------|
| `test_apartment_search.py` | 10 | Listing model, sample data, search tool |
| `test_branching_fsm.py` | 30 | FSM transitions, tool args, prompt rendering |
| `test_calendar_provider.py` | 10 | Calendar ABC, Google Calendar slots, events, booking |
| `test_workflow.py` | 26 | Workflow definition, schema validation |
| `test_debug_tracing.py` | 33 | Debug events, field progress, no PROGRESS leak |
| `test_e2e_voice_harness.py` | 20 | Voice pipeline, TTS/STT, FSM, response quality |

### Browser WebRTC Test

1. Start the server: `./scripts/run.sh`
2. Open http://localhost:8090 in Chrome/Firefox
3. Click **Call**, grant microphone access
4. Check the event log for: ICE servers received, SDP offer/answer, ICE state connected

If aiortc is not installed, the browser will show a clear error message instead of silently disconnecting.

### Twilio Phone Test

1. Expose local server: `ngrok http 8090`
2. Configure Twilio phone number webhook: `https://<ngrok-url>/twilio/voice`
3. Call your Twilio number
4. Speak "I'm looking for a 2 bedroom apartment" and verify the FSM conversation flow

## Project Structure

```
voice-calendar-scheduler-FSM/
├── engine-repo/              # Git submodule — FSM engine
├── engine/                   # Symlink → engine-repo/engine/
├── scheduling/               # Domain application
│   ├── app.py                # FastAPI: Twilio + WebRTC + admin endpoints
│   ├── auth.py               # Bearer token auth (admin + WebSocket)
│   ├── session.py            # Per-call FSM session driver
│   ├── config.py             # Pydantic settings (.env) + startup validation
│   ├── channels/             # Audio normalization (Twilio, WebRTC)
│   ├── workflows/            # FSM step definitions
│   ├── tools/                # Search, calendar, booking tools
│   ├── calendar_providers/   # Re-export shim → cal-provider library
│   └── models/               # Data models (caller state, booking)
├── gateway/                  # WebRTC signaling + TURN
│   ├── server.py             # WebSocket signaling handler
│   ├── webrtc.py             # Engine Session proxy (namespace fix)
│   └── turn.py               # Twilio TURN credential fetch
├── web/                      # Browser client + admin + editor
│   ├── admin.html            # Admin panel (voice, barge-in, config)
│   ├── fsm.html              # FSM viewer (sessions, debug stream)
│   └── editor/               # Visual workflow editor (Vite + React)
├── listings/                 # Apartment data + RAG ingestion
│   ├── import_csv.py         # CSV → JSON import pipeline
│   ├── ingest.py             # JSON → RAG service (single + batch)
│   ├── schema.py             # ApartmentListing Pydantic model
│   ├── sample_data/          # 10 hand-crafted listings (quick-start)
│   └── data/                 # Kaggle-imported listings + column mappings
├── tests/                    # Unit + E2E voice harness + auth tests
├── scripts/
│   ├── setup.sh              # Full project setup (venv, deps, hooks)
│   ├── start.sh              # Start everything (RAG + Backend + Editor)
│   └── run.sh                # Start backend only
├── services.conf             # External service paths (RAG repo location)
├── docker-compose.yml        # RAG service container
├── requirements-lock.txt     # Pinned dependency versions
├── .env.example              # Configuration template
└── CLAUDE.md                 # AI assistant project instructions
```

## Technical Notes

### Namespace Collision Fix

The project has its own `gateway/` package, which shadows the engine-repo's `gateway/` package. The `gateway/webrtc.py` module handles this by temporarily swapping `sys.modules["gateway"]` during import of the engine's WebRTC module, then restoring it. This is transparent to callers.

### Multi-Turn FSM

The engine's WorkflowRunner is designed for single-turn research workflows. `SchedulingSession` adapts this for multi-turn voice conversations by wrapping the Orchestrator per-step with different system prompts and tools, using JSON signal detection to advance between steps.

### Calendar Provider Extraction

The `scheduling/calendar_providers/` directory is a re-export shim — the actual implementations live in the standalone `cal-provider` library (`../cal-provider/`). The FSM tools layer (`tools/calendar.py`, `tools/booking.py`) imports from `scheduling.calendar_providers` as before, and the shim re-exports from `cal_provider`. If you mock-patch Google API calls in tests, target `cal_provider.providers.google.Credentials` and `cal_provider.providers.google.build` (not the shim module).

### Voice Package Dependencies

The voice packages (faster-whisper, piper-tts, aiortc, av) have native dependencies and can be slow to install. Use `--quick` with `setup.sh` to skip them during development. The server handles their absence gracefully with clear error messages.
