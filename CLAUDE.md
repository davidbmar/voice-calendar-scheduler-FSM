# CLAUDE.md — Voice Calendar Scheduler

## What This Is

24/7 voice-driven apartment viewing scheduler. 8-step FSM guides callers (Twilio PSTN or browser WebRTC) through searching listings, checking Google Calendar, and booking viewings — all via natural conversation.

## Commands

```bash
# Setup
./scripts/setup.sh                    # Full setup (venv, deps, submodule)
./scripts/setup.sh --quick            # Skip heavy voice packages

# Run
./scripts/run.sh                      # Start server (default port 9909)
# Or manually:
PYTHONPATH=".:engine-repo" .venv/bin/uvicorn scheduling.app:app --port 9909

# Test
PYTHONPATH=".:engine-repo" .venv/bin/python -m pytest tests/ -v

# RAG service (apartment search)
docker compose up -d rag                      # Start RAG (first boot ~30s for model load)
curl -sf http://localhost:9900/health          # Verify healthy + 0 documents

# Ingest listings (one-time — data persists across restarts)
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.ingest                                          # Sample data (10 listings)
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.ingest --data listings/data/austin_apartments.json  # Kaggle data (535 listings)

# Import new CSV data (optional — requires Kaggle download)
PYTHONPATH=".:engine-repo" .venv/bin/python -m listings.import_csv <csv_path> --city Austin --state TX --output listings/data/austin_apartments.json

# Cloudflare Tunnel (public URL for iPhone/desktop testing)
./scripts/setup_tunnel.sh            # One-time: create named tunnel + optional DNS
./scripts/run.sh --tunnel             # Start server + tunnel (stable URL if configured)
./scripts/run.sh --tunnel             # Without setup: quick tunnel (random *.trycloudflare.com URL)
```

## Architecture

```
scheduling/              Domain app — the core
  app.py                 FastAPI entry: Twilio + WebRTC + admin endpoints
  session.py             Per-call FSM session driver (the brain)
  config.py              Pydantic settings (.env) + runtime_settings dict
  workflows/             FSM schema + JSONL loader
  tools/                 apartment_search, calendar, booking
  channels/              VoiceChannel ABC: Twilio (mulaw 8kHz) + WebRTC (Opus 48kHz) → PCM 16kHz
  calendar_providers/    Google Calendar (pluggable ABC)
  models/                CallerState (progressive), BookingRequest/Response
engine-repo/             Git submodule — orchestrator, LLM, STT, TTS
engine/                  Symlink → engine-repo/engine/
gateway/                 WebRTC signaling + TURN credentials
web/                     Browser client (vanilla JS) + admin panel + visual editor
listings/                Apartment data + RAG ingestion
  import_csv.py          CSV → JSON pipeline (configurable column mapping)
  ingest.py              JSON → RAG service (single + batch mode)
  data/                  Kaggle-imported listings (535 Austin TX)
  sample_data/           10 hand-crafted listings (quick-start)
data/workflows/          FSM definitions as JSONL (apartment_viewing.jsonl)
```

## Key Gotchas

- **PYTHONPATH required**: Always `PYTHONPATH=".:engine-repo"` — engine is a submodule, not installed
- **Namespace collision**: `gateway/` exists in both this project and engine-repo. `gateway/webrtc.py` patches `sys.modules` to resolve this — don't rename or restructure gateway/
- **engine/ is a symlink** to `engine-repo/engine/`. Don't delete or replace
- **Audio normalization**: All paths normalize to 16kHz mono int16 PCM internally. Twilio=mulaw 8kHz, WebRTC=Opus 48kHz
- **Voice packages optional**: faster-whisper, piper-tts, aiortc skipped with `--quick`. Server handles absence gracefully
- **Runtime settings are mutable**: `config.py` has Pydantic env settings AND a `runtime_settings` dict that admin API changes at runtime (read every VAD loop iteration)
- **Conversation history pruning**: `session.py` trims `_messages` to last 20 when exceeding 30
- **JSON signal protocol**: FSM advances when LLM emits fenced JSON blocks with an `intent` field. Works across all LLM providers (Claude/OpenAI/Ollama)
- **RAG data persists**: Docker volume `voice-calendar-scheduler-fsm_rag-data` survives container restarts. Only re-ingest if the volume is removed or you want different data
- **RAG port mapping**: docker-compose maps `9900→8100` (FSM expects 9900, RAG container runs on 8100)
- **Cloudflare Tunnel**: carries HTTP+WebSocket signaling only; WebRTC audio flows peer-to-peer via Twilio TURN. `.tunnel-config` is machine-specific (gitignored). `run.sh --tunnel` skips `exec` so the EXIT trap can kill cloudflared on Ctrl+C

## Data Flow

```
Caller → Twilio PSTN or Browser WebRTC
  → VoiceChannel (audio normalization to PCM 16kHz)
  → VAD (RMS energy threshold) → silence detected
  → STT (faster-whisper base, int8, thread pool)
  → SchedulingSession.handle_utterance(text)
  → FSM state lookup → render system prompt ({{placeholder}} injection)
  → LLM call (orchestrator.chat via thread pool)
  → JSON signal extraction → state transition + CallerState update
  → Tool auto-execution if next state is tool-type
  → TTS (Piper/Kokoro, thread pool) → audio back to channel
```

## FSM Workflow (9 states)

hello → greet_and_gather → search_listings (tool) → present_options → check_availability (tool) → propose_times → collect_details → create_booking (tool) → confirm_done

Branching: any LLM state can → `exit` or loop back (e.g., search_again → greet_and_gather). Defined in `data/workflows/apartment_viewing.jsonl`.

## Environment

Copy `.env.example` → `.env`. Minimum: `LLM_PROVIDER` + API key. See `.env.example` for all options.

## Testing

8 test files, 64 tests. All async (`pytest-asyncio`, `asyncio_mode = auto`).

## API Endpoints

| Path | Purpose |
|------|---------|
| `POST /twilio/voice` | Twilio webhook (returns TwiML) |
| `WS /twilio/stream` | Twilio Media Stream |
| `WS /ws` | WebRTC signaling |
| `GET /health` | Health check |
| `GET/POST /api/config` | Runtime settings |
| `GET /api/fsm/steps` | FSM definitions |
| `PATCH /api/fsm/steps/{id}` | Edit step at runtime |
| `WS /api/fsm/sessions/{id}/debug` | Real-time debug events |
| `GET /admin` | Admin panel |
| `GET /editor` | Visual workflow editor |

---

## Project Memory System

Every coding session, commit, and decision must be documented and searchable.

### Rule 1: Session ID Format

```
S-YYYY-MM-DD-HHMM-<slug>
```
- HHMM is **UTC** (use `date -u +%Y-%m-%d-%H%M`)
- Example: `S-2026-02-14-1430-tts-webrtc-pipeline`

### Rule 2: Commit Message Format

Write a **human-readable subject line**. Put the Session ID in the commit body:
```
Subject line describing the change

Session: S-YYYY-MM-DD-HHMM-slug
```

### Rule 3: Session Documentation

1. **Check if a session exists** for this work:
   ```bash
   ls docs/project-memory/sessions/
   ```
2. **If no session exists, create one:**
   - Copy `docs/project-memory/sessions/_template.md`
   - Name it with the Session ID: `S-YYYY-MM-DD-HHMM-slug.md`
   - Fill in Title, Goal, Context, Plan
3. **After making changes, update the session doc:**
   - Add what changed to "Changes Made"
   - Document decisions in "Decisions Made"
   - Link commits after you create them

### Rule 4: When to Create an ADR

Create an ADR in `docs/project-memory/adr/` when:
- Making significant architectural decisions
- Choosing between technical approaches
- Establishing patterns that will be followed
- Making decisions with long-term consequences

Use the ADR template: `docs/project-memory/adr/_template.md`

### Rule 5: Backlog (Bugs & Features)

Track work items in `docs/project-memory/backlog/`:
- **Bugs** use `B-NNN` prefix (e.g., `B-001-audio-dropout.md`)
- **Features** use `F-NNN` prefix (e.g., `F-003-real-calendar-integration.md`)
- Each item gets its own markdown file with Summary, Status, Priority
- Update `docs/project-memory/backlog/README.md` table when adding/changing items
- Link backlog items from code comments when relevant (e.g., `# MOCK: see F-003`)

### Rule 6: Searching Project Memory

```bash
git log --all --grep="S-YYYY-MM-DD"              # Commits by session
grep -r "keyword" docs/project-memory/sessions/   # Sessions by keyword
grep -r "topic" docs/project-memory/adr/           # ADRs by topic
grep -r "keyword" docs/project-memory/backlog/     # Backlog by keyword
```

### Rule 7: Semantic Search

When users ask questions using **concepts** rather than exact keywords:
1. Read ALL session docs and ADRs
2. Match related concepts (e.g., "mobile" → iPhone, responsive, viewport, iOS)
3. Return results with **explanation** of why they match
4. Cross-reference between sessions, ADRs, and commits

### Workflow

1. **Start of work:** Create or identify Session ID (HHMM is UTC)
2. **Create session doc:** Use template, fill in Title/Goal/Context/Plan
3. **Make changes:** Write code
4. **Commit:** Human-readable subject, `Session: S-...` in body
5. **Update session doc:** Add Changes Made, Decisions, Links
6. **Create ADR if needed:** For significant decisions

### Quick Reference

- **Session template:** `docs/project-memory/sessions/_template.md`
- **ADR template:** `docs/project-memory/adr/_template.md`
- **Overview:** `docs/project-memory/index.md`

### Always Enforce

- Session ID times are UTC (`date -u`)
- Every commit has `Session: S-...` in the body
- Every session has a markdown doc with a Title field
- Significant decisions get ADRs
- Session docs link to commits, PRs, ADRs
