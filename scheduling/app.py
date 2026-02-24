"""FastAPI application — HTTP + WebSocket endpoints for voice scheduling.

Endpoints:

  POST /twilio/voice      Twilio webhook: returns TwiML to connect a Media Stream
  WS   /twilio/stream     Twilio Media Stream WebSocket (mulaw 8kHz audio)
  WS   /ws                WebRTC browser signaling WebSocket
  GET  /health            Health check

The Twilio flow:
  1. Incoming call hits POST /twilio/voice
  2. We return TwiML with <Connect><Stream> pointing to /twilio/stream
  3. Twilio opens a WebSocket to /twilio/stream with mulaw audio
  4. TwilioMediaStreamChannel normalizes to PCM 16kHz for STT

The WebRTC flow:
  1. Browser connects to WS /ws
  2. Sends "hello" → gets ICE servers
  3. Sends "webrtc_offer" with SDP → gets SDP answer
  4. WebRTC peer connection established for audio
"""

from __future__ import annotations

# Load .env into os.environ early — the engine's llm.py reads
# ANTHROPIC_API_KEY via os.getenv() at module import time.
from dotenv import load_dotenv
load_dotenv()

import logging
import time
from xml.etree.ElementTree import Element, SubElement, tostring

# Configure root logger early so all app loggers (gateway.server, etc.)
# have a handler and are visible when run via `uvicorn app:app`.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-7s %(message)s",
)

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from gateway.server import handle_signaling_ws
from scheduling.channels.twilio_channel import TwilioMediaStreamChannel
from scheduling.config import settings
from scheduling.debug_events import get_broadcaster, remove_broadcaster
from scheduling.session import (
    SchedulingSession,
    get_active_sessions,
    get_session,
    register_session,
    unregister_session,
)

log = logging.getLogger("scheduling.app")

_START_TIME = time.time()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Voice Calendar Scheduler",
        description="Voice-driven calendar scheduling with Twilio and WebRTC",
        version="0.1.0",
    )

    # ── Health check ───────────────────────────────────────────

    @app.get("/health")
    async def health() -> JSONResponse:
        """Lightweight health check — confirms the event loop is responsive."""
        uptime = round(time.time() - _START_TIME, 1)
        return JSONResponse({"status": "ok", "uptime": uptime})

    # ── Twilio voice webhook ───────────────────────────────────

    @app.post("/twilio/voice")
    async def twilio_voice(request: Request) -> Response:
        """Twilio webhook for incoming calls.

        Returns TwiML that tells Twilio to open a Media Stream
        WebSocket back to our /twilio/stream endpoint.

        The Stream URL uses wss:// in production (Twilio requires TLS)
        and can fall back to ws:// for local tunneled development.
        """
        # Determine the host for the Stream URL
        host = request.headers.get("host", "localhost:8080")

        # Use wss:// if we're behind a TLS-terminating proxy or tunnel
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        scheme = "wss" if forwarded_proto == "https" else "wss"

        stream_url = f"{scheme}://{host}/twilio/stream"

        # Build TwiML response
        response_el = Element("Response")
        connect_el = SubElement(response_el, "Connect")
        stream_el = SubElement(connect_el, "Stream")
        stream_el.set("url", stream_url)

        twiml = tostring(response_el, encoding="unicode", xml_declaration=True)

        log.info("Twilio voice webhook: connecting stream to %s", stream_url)

        return Response(
            content=twiml,
            media_type="application/xml",
        )

    # ── Twilio Media Stream WebSocket ──────────────────────────

    @app.websocket("/twilio/stream")
    async def twilio_stream(websocket: WebSocket) -> None:
        """Handle Twilio Media Stream WebSocket connection.

        Full pipeline: Twilio audio → STT → SchedulingSession → TTS → Twilio.

        Audio arrives as a continuous stream of small mulaw frames.
        We buffer frames until we detect a silence gap (VAD), then
        transcribe the accumulated audio and feed it to the FSM.
        """
        await websocket.accept()
        log.info("Twilio Media Stream WebSocket connected")

        channel = TwilioMediaStreamChannel(websocket)
        sid = None

        try:
            await channel.initialize()

            caller_info = await channel.get_caller_info()
            log.info(
                "Call from %s (call_sid=%s)",
                caller_info.get("phone_number", "unknown"),
                caller_info.get("call_sid", "unknown"),
            )

            # Create scheduling session with the live (possibly editor-modified) workflow
            live_wf = _workflows.get(WORKFLOW_DEF.id)
            session = _create_session(workflow=live_wf)
            session.start(caller_info)
            sid = register_session(session)
            session.attach_broadcaster(get_broadcaster(sid))

            # Send initial greeting
            greeting = await session.get_greeting()
            log.info("Greeting: %s", greeting[:100])
            await _speak_to_channel(channel, greeting)

            # Main audio processing loop
            # Buffer frames, detect silence, transcribe, respond
            audio_buffer = bytearray()
            silence_frames = 0
            SILENCE_THRESHOLD = 500  # RMS threshold for "silence"
            SILENCE_GAP_FRAMES = 30  # ~600ms of silence at 20ms/frame

            async for frame in channel.receive_audio():
                audio_buffer.extend(frame.samples)

                # Simple energy-based VAD
                rms = _compute_rms(frame.samples)

                if rms < SILENCE_THRESHOLD:
                    silence_frames += 1
                else:
                    silence_frames = 0

                # If we have audio and hit a silence gap, process it
                if len(audio_buffer) > 3200 and silence_frames >= SILENCE_GAP_FRAMES:
                    text = await _transcribe(bytes(audio_buffer))
                    audio_buffer.clear()
                    silence_frames = 0

                    if text and text.strip():
                        log.info("Caller said: %s", text)
                        response = await session.handle_utterance(text)
                        log.info(
                            "Response (step=%s): %s",
                            session.current_step,
                            response[:100],
                        )
                        await _speak_to_channel(channel, response)

                        if session.is_done:
                            log.info("Session complete")
                            break

                # Prevent unbounded buffer growth
                MAX_BUFFER = 16000 * 2 * 30  # 30 seconds of 16kHz int16
                if len(audio_buffer) > MAX_BUFFER:
                    text = await _transcribe(bytes(audio_buffer))
                    audio_buffer.clear()
                    silence_frames = 0
                    if text and text.strip():
                        response = await session.handle_utterance(text)
                        await _speak_to_channel(channel, response)

        except Exception as e:
            log.error("Twilio stream error: %s", e)
        finally:
            if sid:
                remove_broadcaster(sid)
                unregister_session(sid)
            await channel.close()
            log.info("Twilio Media Stream ended")

    # ── WebRTC signaling WebSocket ─────────────────────────────

    @app.websocket("/ws")
    async def ws_signaling(websocket: WebSocket) -> None:
        """WebRTC signaling WebSocket for browser clients."""
        await handle_signaling_ws(websocket)

    # ── Admin page & API ─────────────────────────────────────────

    import os
    from pathlib import Path

    web_dir = Path(__file__).resolve().parent.parent / "web"

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        admin_path = web_dir / "admin.html"
        if admin_path.exists():
            return HTMLResponse(content=admin_path.read_text())
        return HTMLResponse(content="<h1>Admin page not found</h1>", status_code=404)

    @app.get("/api/config")
    async def get_config():
        from scheduling.config import runtime_settings
        return JSONResponse(runtime_settings)

    @app.post("/api/config")
    async def update_config(request: Request):
        from scheduling.config import runtime_settings
        body = await request.json()
        for key in body:
            if key in runtime_settings:
                runtime_settings[key] = body[key]
        log.info("Config updated: %s", runtime_settings)
        return JSONResponse(runtime_settings)

    @app.post("/api/tts/preview")
    async def tts_preview(request: Request):
        """Synthesize text with a given voice and return WAV audio."""
        import asyncio
        import io
        import struct

        body = await request.json()
        text = body.get("text", "").strip()
        voice = body.get("voice", "af_heart")
        engine = body.get("engine", "kokoro")

        if not text:
            return JSONResponse({"error": "No text provided"}, status_code=400)
        if len(text) > 500:
            return JSONResponse({"error": "Text too long (max 500 chars)"}, status_code=400)

        try:
            if engine == "kokoro":
                from kokoro_onnx import Kokoro
                kokoro_dir = Path(__file__).resolve().parent.parent.parent / "kokoro-tts"
                model_path = kokoro_dir / "kokoro-v1.0.onnx"
                voices_path = kokoro_dir / "voices-v1.0.bin"

                kokoro = Kokoro(str(model_path), str(voices_path))

                # Determine language from voice prefix
                lang_map = {
                    'a': 'en-us', 'b': 'en-gb', 'f': 'fr-fr',
                    'i': 'it', 'j': 'ja', 'z': 'cmn',
                }
                lang = lang_map.get(voice[0], 'en-us') if voice else 'en-us'

                loop = asyncio.get_event_loop()
                samples, sample_rate = await loop.run_in_executor(
                    None, lambda: kokoro.create(text, voice=voice, speed=1.0, lang=lang)
                )

                # Convert float samples to int16 WAV
                import numpy as np
                audio_int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
                pcm_bytes = audio_int16.tobytes()

            else:
                # Piper TTS
                from engine.tts import synthesize
                loop = asyncio.get_event_loop()
                pcm_bytes = await loop.run_in_executor(None, synthesize, text, voice)
                sample_rate = 48000  # piper outputs 48kHz after resampling

            # Build WAV in memory
            buf = io.BytesIO()
            num_samples = len(pcm_bytes) // 2
            data_size = num_samples * 2
            # WAV header (44 bytes)
            buf.write(b'RIFF')
            buf.write(struct.pack('<I', 36 + data_size))
            buf.write(b'WAVE')
            buf.write(b'fmt ')
            buf.write(struct.pack('<I', 16))         # chunk size
            buf.write(struct.pack('<H', 1))          # PCM format
            buf.write(struct.pack('<H', 1))          # mono
            buf.write(struct.pack('<I', sample_rate))
            buf.write(struct.pack('<I', sample_rate * 2))  # byte rate
            buf.write(struct.pack('<H', 2))          # block align
            buf.write(struct.pack('<H', 16))         # bits per sample
            buf.write(b'data')
            buf.write(struct.pack('<I', data_size))
            buf.write(pcm_bytes)

            return Response(
                content=buf.getvalue(),
                media_type="audio/wav",
                headers={"Content-Disposition": "inline"},
            )

        except Exception as e:
            log.error("TTS preview error: %s", e, exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/voices")
    async def list_voices():
        """List available voices from kokoro-tts and piper."""
        voices = {"kokoro": [], "piper": []}

        # Kokoro voices
        try:
            from kokoro_onnx import Kokoro
            kokoro_dir = Path(__file__).resolve().parent.parent.parent / "kokoro-tts"
            model_path = kokoro_dir / "kokoro-v1.0.onnx"
            voices_path = kokoro_dir / "voices-v1.0.bin"
            if model_path.exists() and voices_path.exists():
                kokoro = Kokoro(str(model_path), str(voices_path))
                for v in kokoro.get_voices():
                    voices["kokoro"].append(v)
        except Exception as e:
            log.warning("Could not list kokoro voices: %s", e)

        # Piper voices
        try:
            from engine.tts import list_voices as piper_list_voices
            for v in piper_list_voices():
                voices["piper"].append(v["id"])
        except Exception as e:
            log.warning("Could not list piper voices: %s", e)

        return JSONResponse(voices)

    # ── FSM visualization page & API ─────────────────────────────

    @app.get("/fsm")
    async def fsm_page():
        from starlette.responses import RedirectResponse
        return RedirectResponse(url="/editor")

    @app.get("/api/fsm/steps")
    async def get_fsm_steps():
        """Return the FSM step definitions and ordering."""
        import dataclasses
        from scheduling.workflows.apartment_viewing import STEPS, STEP_ORDER
        steps_data = {
            sid: dataclasses.asdict(step) for sid, step in STEPS.items()
        }
        return JSONResponse({"steps": steps_data, "step_order": STEP_ORDER})

    @app.patch("/api/fsm/steps/{step_id}")
    async def update_fsm_step(step_id: str, request: Request):
        """Edit a step's system_prompt, narration, or tool_names at runtime."""
        from scheduling.workflows.apartment_viewing import STEPS
        step = STEPS.get(step_id)
        if not step:
            return JSONResponse({"error": "Step not found"}, status_code=404)

        body = await request.json()
        allowed = {"system_prompt", "narration", "tool_names"}
        updated = []
        for key in body:
            if key in allowed:
                setattr(step, key, body[key])
                updated.append(key)

        if not updated:
            return JSONResponse(
                {"error": "No valid fields. Allowed: " + ", ".join(sorted(allowed))},
                status_code=400,
            )

        log.info("FSM step %s updated: %s", step_id, updated)
        import dataclasses
        return JSONResponse(dataclasses.asdict(step))

    @app.get("/api/fsm/sessions")
    async def list_fsm_sessions():
        """Return summary of all active scheduling sessions."""
        sessions = get_active_sessions()
        return JSONResponse({
            "sessions": [s.to_dict() for s in sessions.values()],
            "count": len(sessions),
        })

    @app.get("/api/fsm/sessions/{session_id}")
    async def get_fsm_session(session_id: str):
        """Return detailed state of a single session."""
        session = get_session(session_id)
        if not session:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        return JSONResponse(session.to_dict(detail=True))

    # ── Test endpoint: create a demo session for UI testing ──────

    @app.post("/api/fsm/sessions/demo")
    async def create_demo_session():
        """Create a fake session for testing the debug UI."""
        import asyncio

        live_wf = _workflows.get(WORKFLOW_DEF.id)
        session = _create_session(workflow=live_wf)
        session.start({"phone_number": "+15551234567", "call_sid": "DEMO"})
        sid = register_session(session)
        session.attach_broadcaster(get_broadcaster(sid))

        # Fire demo events in the background — long initial delay so user
        # has time to click DEBUG before events start flowing
        async def _demo_events():
            # 8s head start to open the debug overlay
            await asyncio.sleep(8)

            # 1. Transition: idle → greet_and_gather
            session._emit_event("transition", {
                "from": "idle", "to": session._current_step_id, "intent": "schedule_viewing",
            })
            await asyncio.sleep(3)
            session._emit_event("llm_call", {
                "system_prompt": "You are a friendly apartment...",
                "user_text": "A caller just connected.",
            })
            await asyncio.sleep(2)
            session._emit_event("llm_response", {
                "response": "Hello! I'd love to help you find the perfect apartment.",
                "has_json_signal": False,
            })

            await asyncio.sleep(4)
            session._emit_event("stt", {"text": "I'm looking for a 2 bedroom near downtown"})
            await asyncio.sleep(2)
            session._emit_event("llm_call", {
                "system_prompt": "You are a friendly apartment...",
                "user_text": "I'm looking for a 2 bedroom near downtown",
            })
            await asyncio.sleep(3)
            session._emit_event("llm_response", {
                "response": "Great! A 2 bedroom near downtown. What's your budget?",
                "has_json_signal": False,
            })

            await asyncio.sleep(4)
            session._emit_event("stt", {"text": "Around 2000 a month"})
            await asyncio.sleep(2)
            session._emit_event("llm_response", {
                "response": "Perfect! Let me search for 2BR apartments near downtown under $2000.",
                "has_json_signal": True,
            })
            session._emit_event("step_complete", {
                "extracted_data": {"bedrooms": 2, "area": "downtown", "budget": 2000},
            })

            # 2. Transition: greet_and_gather → search_listings
            await asyncio.sleep(2)
            session._current_step_id = "search_listings"
            session._emit_event("transition", {
                "from": "greet_and_gather", "to": "search_listings", "intent": "gathered",
            })
            await asyncio.sleep(3)
            session._emit_event("tool_exec", {
                "tool_name": "apartment_search",
                "args": {"query": "2 bedroom near downtown under $2000"},
                "result": "Found 5 listings matching criteria",
            })

            # 3. Transition: search_listings → present_options
            await asyncio.sleep(3)
            session._current_step_id = "present_options"
            session._emit_event("transition", {
                "from": "search_listings", "to": "present_options", "intent": "success",
            })
            await asyncio.sleep(3)
            session._emit_event("llm_response", {
                "response": "I found 5 apartments! Here are your top 3...",
                "has_json_signal": False,
            })

        asyncio.create_task(_demo_events())

        return JSONResponse({"session_id": sid, "message": "Demo session created"})

    # ── Debug stream WebSocket ──────────────────────────────────

    @app.websocket("/api/fsm/sessions/{session_id}/debug")
    async def debug_stream(websocket: WebSocket, session_id: str) -> None:
        """WebSocket endpoint that streams real-time debug events."""
        session = get_session(session_id)
        if not session:
            await websocket.close(code=4004, reason="Session not found")
            return

        await websocket.accept()
        broadcaster = get_broadcaster(session_id)
        session.attach_broadcaster(broadcaster)
        queue = broadcaster.subscribe()

        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.warning("Debug stream error for %s: %s", session_id, e)
        finally:
            broadcaster.unsubscribe(queue)

    @app.post("/api/fsm/sessions/{session_id}/pause")
    async def pause_session(session_id: str):
        """Pause FSM processing for a session."""
        session = get_session(session_id)
        if not session:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        session.pause()
        return JSONResponse({"paused": True})

    @app.post("/api/fsm/sessions/{session_id}/resume")
    async def resume_session(session_id: str):
        """Resume FSM processing for a session."""
        session = get_session(session_id)
        if not session:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        session.resume()
        return JSONResponse({"paused": False})

    @app.get("/api/fsm/sessions/{session_id}/debug-context")
    async def get_debug_context(session_id: str):
        """Return full debug snapshot for Claude context."""
        session = get_session(session_id)
        if not session:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        data = session.to_dict(detail=True)
        data["duration"] = round(time.time() - data.get("started_at", time.time()), 1)
        return JSONResponse(data)

    # ── Branching workflow API ───────────────────────────────────

    # Registry of loaded workflows (keyed by workflow ID)
    from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
    from scheduling.workflows.loader import load_workflow_jsonl, save_workflow_jsonl
    from scheduling.workflows.schema import SchedulingWorkflowDef

    _workflows: dict[str, SchedulingWorkflowDef] = {WORKFLOW_DEF.id: WORKFLOW_DEF}
    _workflow_paths: dict[str, Path] = {
        WORKFLOW_DEF.id: Path(__file__).resolve().parent.parent / "data" / "workflows" / "apartment_viewing.jsonl",
    }

    @app.get("/api/workflow/{workflow_id}")
    async def get_workflow(workflow_id: str):
        """Return full workflow definition as JSON."""
        wf = _workflows.get(workflow_id)
        if not wf:
            return JSONResponse({"error": "Workflow not found"}, status_code=404)
        return JSONResponse(wf.model_dump())

    @app.patch("/api/workflow/{workflow_id}/states/{state_id}")
    async def update_workflow_state(workflow_id: str, state_id: str, request: Request):
        """Edit any state field at runtime."""
        wf = _workflows.get(workflow_id)
        if not wf:
            return JSONResponse({"error": "Workflow not found"}, status_code=404)
        state = wf.states.get(state_id)
        if not state:
            return JSONResponse({"error": "State not found"}, status_code=404)

        body = await request.json()
        updated = []
        for key, value in body.items():
            if hasattr(state, key) and key != "id":
                setattr(state, key, value)
                updated.append(key)

        if not updated:
            return JSONResponse({"error": "No valid fields updated"}, status_code=400)

        log.info("Workflow %s state %s updated: %s", workflow_id, state_id, updated)
        return JSONResponse(state.model_dump())

    @app.put("/api/workflow/{workflow_id}")
    async def save_workflow(workflow_id: str, request: Request):
        """Save complete workflow (from editor) and persist to JSONL."""
        body = await request.json()

        try:
            # Parse and validate
            from scheduling.workflows.loader import _parse_workflow
            wf = _parse_workflow(body)
        except Exception as e:
            return JSONResponse({"error": f"Invalid workflow: {e}"}, status_code=400)

        # Update in-memory registry
        _workflows[workflow_id] = wf

        # Persist to JSONL file
        jsonl_path = _workflow_paths.get(workflow_id)
        if jsonl_path:
            save_workflow_jsonl(wf, jsonl_path)
            log.info("Workflow %s persisted to %s", workflow_id, jsonl_path)
        else:
            # New workflow — save to default location
            jsonl_path = Path(__file__).resolve().parent.parent / "data" / "workflows" / f"{workflow_id}.jsonl"
            save_workflow_jsonl(wf, jsonl_path)
            _workflow_paths[workflow_id] = jsonl_path
            log.info("New workflow %s saved to %s", workflow_id, jsonl_path)

        return JSONResponse(wf.model_dump())

    # ── Visual editor serving ─────────────────────────────────────

    editor_dist = Path(__file__).resolve().parent.parent / "web" / "editor" / "dist"

    @app.get("/editor", response_class=HTMLResponse)
    async def editor_page():
        """Serve the visual workflow editor."""
        index_path = editor_dist / "index.html"
        if index_path.exists():
            return HTMLResponse(content=index_path.read_text())
        # Fallback: try dev index.html
        dev_index = Path(__file__).resolve().parent.parent / "web" / "editor" / "index.html"
        if dev_index.exists():
            return HTMLResponse(content=dev_index.read_text())
        return HTMLResponse(
            content="<h1>Editor not built</h1><p>Run <code>cd web/editor && npm run build</code></p>",
            status_code=404,
        )

    # Mount editor static files if built
    if editor_dist.is_dir():
        app.mount("/editor/assets", StaticFiles(directory=str(editor_dist / "assets")), name="editor-assets")

    # ── Static file serving (browser client) ───────────────────

    if web_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/", response_class=HTMLResponse)
        async def index() -> HTMLResponse:
            """Serve the browser client."""
            index_path = web_dir / "index.html"
            if index_path.exists():
                return HTMLResponse(content=index_path.read_text())
            return HTMLResponse(content="<h1>Voice Calendar Scheduler</h1>")

    return app


# ── Helper functions ──────────────────────────────────────────────

def _create_session(workflow=None) -> SchedulingSession:
    """Create a SchedulingSession with configured providers.

    Args:
        workflow: Optional SchedulingWorkflowDef to use. When provided,
                  the session uses this (live-edited) workflow instead of
                  the stale module-level default. This is how editor changes
                  take effect without a server restart.
    """
    calendar_provider = None
    if settings.google_service_account_json:
        try:
            from scheduling.calendar_providers.google import GoogleCalendarProvider
            calendar_provider = GoogleCalendarProvider(
                service_account_path=settings.google_service_account_json,
            )
        except Exception as e:
            log.warning("Google Calendar not configured: %s", e)

    return SchedulingSession(
        workflow=workflow,
        calendar_provider=calendar_provider,
        calendar_id=settings.google_calendar_id,
    )


async def _transcribe(audio_bytes: bytes) -> str:
    """Transcribe PCM 16kHz audio using faster-whisper."""
    import asyncio

    try:
        from engine.stt import transcribe

        loop = asyncio.get_event_loop()
        text, no_speech_prob, avg_logprob = await loop.run_in_executor(
            None, transcribe, audio_bytes, 16000
        )

        # Filter low-quality transcriptions
        if no_speech_prob > 0.6:
            return ""
        return text
    except ImportError:
        log.warning("STT engine not available — returning empty transcription")
        return ""


async def _speak_to_channel(channel, text: str) -> None:
    """Synthesize text to audio and send through the voice channel."""
    import asyncio

    try:
        from engine.tts import synthesize

        from scheduling.channels.base import AudioFrame

        loop = asyncio.get_event_loop()
        # Piper TTS outputs 48kHz — we need 16kHz for the channel
        pcm_48k = await loop.run_in_executor(None, synthesize, text)

        if pcm_48k:
            import numpy as np

            samples_48k = np.frombuffer(pcm_48k, dtype=np.int16)
            # Downsample 48kHz → 16kHz (take every 3rd sample)
            samples_16k = samples_48k[::3]
            pcm_16k = samples_16k.tobytes()

            frame = AudioFrame(samples=pcm_16k, sample_rate=16000)
            await channel.send_audio([frame])
    except ImportError:
        log.warning("TTS engine not available — skipping audio response")


def _compute_rms(pcm_bytes: bytes) -> float:
    """Compute RMS energy of int16 PCM audio."""
    import numpy as np

    if len(pcm_bytes) < 2:
        return 0.0
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples**2)))


# ── Module-level app instance for uvicorn ──────────────────────

app = create_app()


if __name__ == "__main__":
    import os
    import signal
    import subprocess
    import uvicorn

    # Kill any stale process holding our port from a previous run
    port = settings.port
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True,
        )
        for pid_str in result.stdout.strip().split():
            pid = int(pid_str)
            if pid != os.getpid():
                os.kill(pid, signal.SIGKILL)
                log.info("Killed stale process %d on port %d", pid, port)
    except Exception:
        pass

    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = (
        "%(asctime)s %(name)-12s %(levelname)-8s %(message)s"
    )

    uvicorn.run(
        "scheduling.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=log_config,
    )
