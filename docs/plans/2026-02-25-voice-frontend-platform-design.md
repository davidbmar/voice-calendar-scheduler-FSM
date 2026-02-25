# Voice Frontend Platform — Design Document

**Date:** 2026-02-25
**Status:** Approved
**Goal:** Extract shared voice WebRTC infrastructure into a reusable monorepo (`voice-frontend`) that any voice application can consume.

---

## Problem

Two voice apps (`voice-calendar-scheduler-FSM` and `iphone-and-companion-transcribe-mode`) duplicate the same WebRTC plumbing: TURN credential fetching, ICE gathering, signaling, Cloudflare tunnel scripts, and audio handling. Each new voice app re-solves these problems. The engine-repo submodule causes namespace collisions and import hacks.

## Solution

One monorepo (`voice-frontend`) containing three independent packages:

```
voice-frontend/
  packages/
    transport/          Core: TURN, ICE, signaling, tunnel
    edge-auth/          Core: pluggable auth at the edge
    engine-starter/     Reference impl: basic STT, TTS, LLM for testing
  docs/
  examples/
    minimal-voice-app/  "Hello world" — working call in <50 lines
```

Consumer apps remain separate repos and install from `voice-frontend`:

```
voice-calendar-scheduler-FSM/      imports transport, edge-auth
iphone-and-companion-transcribe-mode/  imports transport, edge-auth
future-voice-app/                  imports all three, writes only business logic
```

---

## Package 1: Transport

**Purpose:** Get audio between a browser and a Python server across any network.

### Server-side (Python)

#### `transport.turn`

Fetch ephemeral TURN/STUN credentials from Twilio NTS.

```python
from transport.turn import TwilioTURN

turn = TwilioTURN()  # reads TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN from env
ice_servers = await turn.fetch()
# Returns: [{"urls": "turn:global.turn.twilio.com:3478", "username": "...", "credential": "..."}]
```

**Extracted from:** `gateway/turn.py` (both projects, nearly identical).

**Extensibility:** `TURNProvider` ABC allows future providers (self-hosted coturn, Metered.ca, Xirsys):

```python
class TURNProvider(ABC):
    async def fetch_ice_servers(self) -> list[dict]:
        """Return ICE server dicts in WebRTC format."""
        ...

class TwilioTURN(TURNProvider): ...
class StaticICE(TURNProvider):
    """Fallback: return a fixed list of STUN/TURN servers."""
    ...
```

#### `transport.signaling`

WebSocket signaling server for SDP offer/answer exchange.

```python
from transport.signaling import SignalingServer

signaling = SignalingServer(
    turn_provider=TwilioTURN(),
    on_session=handle_call,       # your callback
)

# In FastAPI:
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await signaling.handle(websocket)
```

**Protocol (unchanged from current):**

```
Client → Server:
  {"type": "hello"}                        → request ICE servers
  {"type": "webrtc_offer", "sdp": "..."}   → SDP offer (with ICE candidates)
  {"type": "ping"}                         → keepalive

Server → Client:
  {"type": "hello_ack", "ice_servers": [...]}  → ICE server list
  {"type": "webrtc_answer", "sdp": "..."}      → SDP answer
  {"type": "error", "message": "..."}          → error
  {"type": "pong"}                             → keepalive ack
```

**Extracted from:** `gateway/server.py:handle_signaling_ws()` (lines 419-517 in scheduler).

#### `transport.session`

WebRTC peer connection lifecycle (wraps aiortc).

```python
class WebRTCSession:
    """One active call. Provides mic audio input and speaker output."""

    async def handle_offer(self, sdp: str) -> str:
        """Process browser SDP offer, return SDP answer."""

    async def speak(self, text: str, tts: TTSProvider) -> float:
        """Synthesize and play audio. Returns duration in seconds."""

    async def listen(self, stt: STTProvider) -> AsyncIterator[str]:
        """Yield transcribed utterances using VAD + STT."""

    async def close(self) -> None:
        """Tear down peer connection."""
```

**Extracted from:** `engine-repo/gateway/webrtc.py` Session class + `gateway/server.py` voice loop logic.

**Key design decision:** The session exposes `speak()` and `listen()` as high-level APIs. VAD parameters (energy threshold, silence gap, confirm frames) are configurable and live-tunable via a settings dict, matching the current admin panel behavior.

#### `transport.tunnel`

Cloudflare tunnel management.

```python
from transport.tunnel import CloudflareTunnel

tunnel = CloudflareTunnel(
    local_port=8090,
    config_path=".tunnel-config",  # persists tunnel name/URL
)
tunnel.start()    # named tunnel if configured, quick tunnel otherwise
print(tunnel.url) # https://scheduler.chattychapters.com or random *.trycloudflare.com
tunnel.stop()     # cleanup on shutdown
```

**Extracted from:** `scripts/setup_tunnel.sh` and `scripts/run.sh --tunnel` (both projects).

#### `transport.audio`

Audio format utilities.

```python
from transport.audio import resample, AudioQueue

# Resample between WebRTC (48kHz) and STT/TTS (16kHz)
pcm_16k = resample(pcm_48k, from_rate=48000, to_rate=16000)
pcm_48k = resample(pcm_16k, from_rate=16000, to_rate=48000)

# Thread-safe FIFO for TTS output (never drops audio)
queue = AudioQueue()
queue.enqueue(pcm_bytes)
chunk = queue.read(num_bytes)
```

**Extracted from:** `scheduling/channels/webrtc_channel.py` (resampling) and `engine-repo/gateway/audio/audio_queue.py`.

### Client-side (JavaScript)

#### `transport/js/voice-webrtc-client.js`

Drop-in browser client. Framework-agnostic vanilla JS.

```javascript
import { VoiceWebRTCClient } from 'voice-frontend/transport';

const client = new VoiceWebRTCClient({
    signalingUrl: '/ws',           // default
    audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        sampleRate: 48000,
    },
    iceGatheringTimeout: 10000,     // 10s, then proceed with partial candidates
});

client.on('ready', () => console.log('Signaling connected, ICE servers received'));
client.on('connected', () => console.log('Call active — audio flowing'));
client.on('failed', (reason) => console.log('ICE failed:', reason));
client.on('ended', () => console.log('Call ended'));
client.on('log', (msg, level) => console.log(`[${level}] ${msg}`));

document.getElementById('call-btn').onclick = () => {
    if (client.inCall) client.hangUp();
    else client.startCall();
};
```

**Extracted from:** `web/app.js` (scheduler) and `web/app.js` + `web/desktop-app.js` (companion).

**Key behaviors preserved:**
- Waits for ICE gathering before sending SDP offer (the bug we just fixed)
- 10s timeout on ICE gathering with partial candidate fallback
- `playsInline = true` for Safari audio playback
- 30s keepalive pings
- Automatic cleanup on disconnect

---

## Package 2: Edge Auth

**Purpose:** Control who can access voice endpoints. Pluggable providers, decoupled from transport.

### Providers

```python
from edge_auth import AuthProvider

class AuthProvider(ABC):
    async def authenticate(self, request: Request) -> AuthResult:
        """Validate the request. Return AuthResult with user info or raise."""
        ...

    async def authenticate_ws(self, websocket: WebSocket) -> AuthResult:
        """Validate a WebSocket connection (typically via query param)."""
        ...

@dataclass
class AuthResult:
    authenticated: bool
    user_id: str | None = None
    user_email: str | None = None
    user_name: str | None = None
    provider: str = ""          # "cloudflare", "google", "bearer", "none"
```

### Implementations

```python
from edge_auth.providers import (
    CloudflareAccessProvider,   # validates CF-Access-JWT-Assertion header
    GoogleJWTProvider,          # validates Google OAuth2 ID tokens
    BearerTokenProvider,        # simple API key in Authorization header
    NoAuthProvider,             # allows everything (local dev)
)
```

### FastAPI Integration

```python
from edge_auth import auth_middleware, auth_dependency

# Option 1: Middleware (protects all endpoints)
auth = CloudflareAccessProvider(team_domain="myteam.cloudflareaccess.com")
app.add_middleware(auth_middleware(auth, exclude=["/health", "/ws"]))

# Option 2: Dependency (protects specific endpoints)
require_auth = auth_dependency(auth)

@app.get("/api/config", dependencies=[Depends(require_auth)])
async def get_config(): ...

# Option 3: Composite (try multiple providers in order)
from edge_auth import CompositeProvider
auth = CompositeProvider([
    CloudflareAccessProvider(...),   # try CF Access first
    BearerTokenProvider(key="..."),  # fall back to API key
    NoAuthProvider() if DEBUG else None,  # allow in dev
])
```

### WebSocket Auth

```python
# Browser can't send headers, so WebSocket auth uses query params:
# ws://host/ws?token=xxx

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    user = await auth.authenticate_ws(websocket)  # checks ?token=
    await signaling.handle(websocket, user=user)
```

**Extracted from:** `scheduling/auth.py` (scheduler) and `gateway/auth.py` (companion).

---

## Package 3: Engine Starter

**Purpose:** Reference implementation of STT, TTS, and LLM so you can make a test call immediately. **This is explicitly a starter — not production-grade.** Documentation, admin panel, and config page all note this clearly.

### Provider Interfaces (the contracts)

```python
class STTProvider(ABC):
    """Speech-to-text: audio bytes → text."""
    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> TranscriptionResult: ...

class TTSProvider(ABC):
    """Text-to-speech: text → audio bytes."""
    async def synthesize(self, text: str, voice: str = "") -> bytes: ...
    def list_voices(self) -> list[str]: ...

class LLMProvider(ABC):
    """Language model: messages → response text."""
    async def chat(self, messages: list[dict]) -> str: ...

@dataclass
class TranscriptionResult:
    text: str
    no_speech_probability: float = 0.0
    language: str = ""
```

These interfaces live in the package but are meant to be implemented by users with their production providers (Deepgram, ElevenLabs, Claude API, etc.).

### Starter Implementations

```python
from engine_starter import StarterSTT, StarterTTS, StarterLLM

stt = StarterSTT()       # faster-whisper, base model, int8, CPU
tts = StarterTTS()       # Piper (default) or Kokoro
llm = StarterLLM()       # Ollama local, or Claude/OpenAI with API key
```

### Visibility Requirements

The starter engine must be clearly identified everywhere it appears:

1. **Admin panel** — Badge/banner: "Running: Starter Engine — For production, see docs/engines.md"
2. **Config page** — Note under TTS/STT/LLM sections: "Starter engine. Swap providers in config."
3. **Server startup log** — `INFO: Using Starter Engine (STT=whisper-base, TTS=piper, LLM=ollama). See docs for production providers.`
4. **README** — Dedicated section: "Starter Engine" explaining it's for testing and how to swap.
5. **Health endpoint** — Include `"engine": "starter"` in response so monitoring can detect it.

### Extracted from

- `engine-repo/engine/stt.py` → `StarterSTT`
- `engine-repo/engine/tts.py` → `StarterTTS`
- `engine-repo/engine/llm.py` → `StarterLLM`
- `engine-repo/engine/orchestrator.py` → stays app-specific (the FSM/orchestration logic is business logic, not infrastructure)

---

## How Apps Consume This

### Minimal Example (future new app, <50 lines)

```python
from fastapi import FastAPI, WebSocket
from transport.turn import TwilioTURN
from transport.signaling import SignalingServer
from transport.session import WebRTCSession
from engine_starter import StarterSTT, StarterTTS, StarterLLM

app = FastAPI()
stt, tts, llm = StarterSTT(), StarterTTS(), StarterLLM()

async def handle_call(session: WebRTCSession):
    await session.speak("Hi! How can I help?", tts)
    async for utterance in session.listen(stt):
        response = await llm.chat([
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": utterance},
        ])
        await session.speak(response, tts)

signaling = SignalingServer(turn_provider=TwilioTURN(), on_session=handle_call)

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await signaling.handle(websocket)
```

### Migration Path for Existing Apps

**voice-calendar-scheduler-FSM:**
1. Replace `gateway/turn.py` → `from transport.turn import TwilioTURN`
2. Replace `gateway/server.py` signaling logic → `from transport.signaling import SignalingServer`
3. Replace `gateway/webrtc.py` proxy → `from transport.session import WebRTCSession`
4. Replace `web/app.js` → include `voice-webrtc-client.js`
5. Keep `scheduling/session.py` (FSM logic) — that's business logic, stays in the app
6. Remove engine-repo submodule

**iphone-and-companion-transcribe-mode:**
1. Same transport replacements
2. Replace `gateway/auth.py` → `from edge_auth.providers import GoogleJWTProvider`
3. Keep transcription/dictation UI logic — that's business logic

---

## Module Boundaries

```
                    ┌─────────────────────────────┐
                    │        Your Voice App        │
                    │   (FSM, business logic,      │
                    │    conversation design)       │
                    └──────┬──────────┬────────────┘
                           │          │
              ┌────────────▼──┐  ┌────▼───────────┐
              │   Transport   │  │   Edge Auth     │
              │               │  │                 │
              │  TURN         │  │  CF Access      │
              │  Signaling    │  │  Google JWT     │
              │  Session      │  │  Bearer Token   │
              │  Tunnel       │  │  No Auth (dev)  │
              │  Audio utils  │  │                 │
              │  JS client    │  │                 │
              └──────┬────────┘  └─────────────────┘
                     │
              ┌──────▼────────┐
              │ Engine Starter │  ← "Swap me for production"
              │               │
              │  STT (whisper) │
              │  TTS (piper)   │
              │  LLM (ollama)  │
              │               │
              │  Defines ABCs  │
              │  that prod     │
              │  providers     │
              │  implement     │
              └───────────────┘
```

**Rule:** Transport never imports Edge Auth. Edge Auth never imports Transport. Either can be used alone. Engine Starter is optional — transport works with any STT/TTS/LLM that implements the ABCs.

---

## Packaging

**Monorepo structure:**

```
voice-frontend/
  packages/
    transport/
      pyproject.toml        ← "voice-frontend-transport"
      transport/
        __init__.py
        turn.py
        signaling.py
        session.py
        tunnel.py
        audio.py
      js/
        voice-webrtc-client.js
      tests/

    edge-auth/
      pyproject.toml        ← "voice-frontend-edge-auth"
      edge_auth/
        __init__.py
        providers/
          cloudflare.py
          google.py
          bearer.py
          none.py
        middleware.py
        composite.py
      tests/

    engine-starter/
      pyproject.toml        ← "voice-frontend-engine-starter"
      engine_starter/
        __init__.py
        stt.py
        tts.py
        llm.py
        interfaces.py       ← ABCs (STTProvider, TTSProvider, LLMProvider)
      tests/

  examples/
    minimal-voice-app/      ← <50 line working example
    with-auth/              ← example with Cloudflare Access
    custom-engine/          ← example swapping in a production provider

  docs/
    getting-started.md
    transport.md
    edge-auth.md
    engine-starter.md
    swapping-engines.md

  README.md
```

**Installation:**

```bash
pip install voice-frontend-transport                  # just connectivity
pip install voice-frontend-edge-auth                  # just auth
pip install voice-frontend-engine-starter             # just the starter engine
pip install voice-frontend[all]                       # everything
```

---

## Open Questions for Implementation

1. **JS distribution:** Ship `voice-webrtc-client.js` as a static file in the Python package, or publish to npm separately?
2. **VAD location:** VAD (voice activity detection) is currently in the voice loop. Should it be part of transport (generic) or stay app-specific?
3. **Tunnel scripts:** Python wrapper around cloudflared, or keep as shell scripts?
4. **Engine Starter models:** Ship model files in the package, or download on first run?

---

## Success Criteria

1. A new voice app can be built in <50 lines by importing the three packages
2. Existing apps (scheduler, companion) can migrate incrementally
3. Swapping auth providers is a one-line config change
4. Swapping STT/TTS/LLM providers requires only implementing the ABC
5. Admin panel and config page clearly show when starter engine is in use
6. Transport works independently of auth and engine choices
