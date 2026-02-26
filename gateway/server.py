"""WebSocket signaling server for WebRTC browser clients.

A minimal signaling server that handles the WebRTC connection setup
dance over WebSocket.  This is separate from the Twilio endpoints
(which live in scheduling/app.py) and focuses on browser-to-server
WebRTC signaling.

Protocol messages:

  Client → Server:
    {"type": "hello"}                          → request ICE servers
    {"type": "webrtc_offer", "sdp": "..."}     → send SDP offer

  Server → Client:
    {"type": "hello_ack", "ice_servers": [...]} → ICE server list
    {"type": "webrtc_answer", "sdp": "..."}    → SDP answer
    {"type": "error", "message": "..."}        → error

After signaling, the server wires the WebRTC audio through:
  Browser mic → VAD (energy-based silence detection) → STT → FSM
  → LLM response → TTS → WebRTC audio track → Browser speaker
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from gateway.turn import fetch_twilio_turn_credentials
from gateway.webrtc import Session, get_fallback_ice_servers

log = logging.getLogger("gateway.server")


def _create_scheduling_session():
    """Create a SchedulingSession with configured providers.

    Imported lazily to avoid circular imports (scheduling.app imports
    gateway.server, so we can't import scheduling.* at module level).
    """
    from scheduling.config import settings
    from scheduling.session import SchedulingSession

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
        calendar_provider=calendar_provider,
        calendar_id=settings.google_calendar_id,
    )


def _compute_rms(pcm_bytes: bytes) -> float:
    """Compute RMS energy of int16 PCM audio."""
    import numpy as np

    if len(pcm_bytes) < 2:
        return 0.0
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples**2)))


async def _transcribe_webrtc(pcm_48k: bytes) -> str:
    """Transcribe 48kHz int16 PCM using the engine's STT (resamples internally)."""
    try:
        from engine.stt import transcribe

        loop = asyncio.get_event_loop()
        text, no_speech_prob, _ = await loop.run_in_executor(
            None, transcribe, pcm_48k, 48000
        )
        if no_speech_prob > 0.6:
            return ""
        return text
    except ImportError:
        log.warning("STT not available")
        return ""


async def _speak(session, text: str) -> float:
    """Synthesize and speak text using the configured TTS engine/voice.

    Routes between kokoro and piper based on runtime_settings.
    Returns playback duration in seconds.
    """
    from scheduling.config import runtime_settings

    engine = runtime_settings.get("tts_engine", "piper")
    voice = runtime_settings.get("tts_voice", "")

    if engine == "kokoro" and voice:
        return await _speak_kokoro(session, text, voice)
    else:
        # Fall back to piper
        piper_voice = voice if engine == "piper" and voice else "en_US-lessac-medium"
        return await session.speak_text(text, piper_voice)


async def _speak_kokoro(session, text: str, voice: str) -> float:
    """Synthesize with kokoro and enqueue into session's audio pipeline."""
    from pathlib import Path

    import numpy as np
    from kokoro_onnx import Kokoro

    kokoro_dir = Path(__file__).resolve().parent.parent.parent / "kokoro-tts"
    model_path = str(kokoro_dir / "kokoro-v1.0.onnx")
    voices_path = str(kokoro_dir / "voices-v1.0.bin")

    # Language from voice prefix
    lang_map = {
        'a': 'en-us', 'b': 'en-gb', 'f': 'fr-fr',
        'i': 'it', 'j': 'ja', 'z': 'cmn',
    }
    lang = lang_map.get(voice[0], 'en-us') if voice else 'en-us'

    real = session._real
    # Ensure TTS generator is attached
    real._audio_source.set_generator(real._tts_generator)

    # Clean text the same way the engine does
    clean_text = real._clean_for_speech(text)
    sentences = real._split_sentences(clean_text)
    log.info("Kokoro TTS (%s/%s): %d sentences", voice, lang, len(sentences))

    loop = asyncio.get_event_loop()
    kokoro = Kokoro(model_path, voices_path)
    total_bytes = 0

    for i, sentence in enumerate(sentences):
        samples, sr = await loop.run_in_executor(
            None, lambda s=sentence: kokoro.create(s, voice=voice, speed=1.0, lang=lang)
        )
        if samples is not None and len(samples) > 0:
            # Convert float → int16
            audio_int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
            # Resample from kokoro's rate (typically 24kHz) to 48kHz
            if sr != 48000:
                from scipy.signal import resample
                num_target = int(len(audio_int16) * 48000 / sr)
                audio_float = audio_int16.astype(np.float64)
                resampled = resample(audio_float, num_target)
                audio_int16 = np.clip(resampled, -32768, 32767).astype(np.int16)

            pcm_bytes = audio_int16.tobytes()
            real._audio_queue.enqueue(pcm_bytes)
            total_bytes += len(pcm_bytes)
            log.debug("Kokoro sentence %d/%d: %d bytes", i + 1, len(sentences), len(pcm_bytes))

    duration = total_bytes / (48000 * 2)  # 48kHz int16
    log.info("Kokoro TTS total: %d bytes, %.1fs playback", total_bytes, duration)
    return duration


async def _wait_for_playback(real, duration: float, session) -> bool:
    """Wait for TTS playback, optionally detecting barge-in.

    Returns True if interrupted by user speech, False if played to completion.
    On barge-in, preserves recent mic frames (the speech that triggered it)
    instead of discarding them, then transcribes them for debug logging.
    """
    from scheduling.config import runtime_settings

    POLL_INTERVAL = 0.1  # 100ms
    BARGE_IN_THRESHOLD = runtime_settings.get("barge_in_energy_threshold", 1500)
    BARGE_IN_CONFIRM = runtime_settings.get("barge_in_confirm_frames", 5)  # ~500ms sustained speech
    FRAMES_PER_POLL = 5  # ~20ms per mic frame at 48kHz, 5 frames per 100ms poll
    elapsed = 0.0
    barge_confirm = 0
    poll_num = 0
    max_rms_seen = 0.0

    log.info("[BARGE-IN] Playback started: duration=%.1fs, threshold=%d, confirm_needed=%d, enabled=%s",
             duration, BARGE_IN_THRESHOLD, BARGE_IN_CONFIRM, runtime_settings.get("barge_in_enabled", False))

    while elapsed < duration:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        poll_num += 1

        if not runtime_settings.get("barge_in_enabled", False):
            continue  # just sleep, don't check mic

        # Check mic frames that arrived since last poll.
        # Instead of only the last frame, check max RMS across recent frames
        # to avoid missing speech due to frame-boundary sampling.
        if real._mic_frames:
            recent_frames = real._mic_frames[-FRAMES_PER_POLL:]
            rms_values = [_compute_rms(f) for f in recent_frames]
            rms = max(rms_values) if rms_values else 0.0
            if rms > max_rms_seen:
                max_rms_seen = rms

            # Log every poll so we can see what's happening
            log.info("[BARGE-IN] poll #%d elapsed=%.1f/%0.1fs rms=%.0f (max_seen=%.0f) threshold=%d confirm=%d/%d frames=%d",
                     poll_num, elapsed, duration, rms, max_rms_seen, BARGE_IN_THRESHOLD,
                     barge_confirm, BARGE_IN_CONFIRM, len(real._mic_frames))

            if rms >= BARGE_IN_THRESHOLD:
                barge_confirm += 1
                if barge_confirm >= BARGE_IN_CONFIRM:
                    log.info("[BARGE-IN] >>> CONFIRMED! User speech detected (rms=%.0f, %d consecutive frames), stopping TTS <<<",
                             rms, barge_confirm)
                    session.stop_speaking()
                    # Keep recent speech frames instead of clearing everything.
                    frames_to_keep = (barge_confirm + 1) * FRAMES_PER_POLL
                    real._mic_frames = real._mic_frames[-frames_to_keep:]
                    preserved_bytes = sum(len(f) for f in real._mic_frames)
                    log.info("[BARGE-IN] Preserved %d frames (%d bytes, ~%.0fms) for transcription",
                             len(real._mic_frames), preserved_bytes, preserved_bytes / (48000 * 2) * 1000)
                    # Debug: transcribe the preserved frames to show what was captured
                    await _log_barge_in_transcript(real._mic_frames)
                    return True
            else:
                if barge_confirm > 0:
                    log.info("[BARGE-IN] Confirm reset (was %d, rms=%.0f dropped below %d)", barge_confirm, rms, BARGE_IN_THRESHOLD)
                barge_confirm = 0  # must be consecutive

    # Played to completion — check if user started speaking at the tail end
    log.info("[BARGE-IN] Playback complete (%.1fs). Checking tail frames... (max_rms_seen=%.0f)", duration, max_rms_seen)
    if real._mic_frames:
        # Check last few polls worth of frames
        tail_frames = real._mic_frames[-FRAMES_PER_POLL * 3:]
        tail_rms_values = [_compute_rms(f) for f in tail_frames]
        tail_rms = max(tail_rms_values) if tail_rms_values else 0.0
        log.info("[BARGE-IN] Tail check: %d frames, max_rms=%.0f, threshold=%d",
                 len(tail_frames), tail_rms, BARGE_IN_THRESHOLD)
        if tail_rms >= BARGE_IN_THRESHOLD:
            # User is speaking as TTS ended — preserve recent frames
            frames_to_keep = FRAMES_PER_POLL * 3  # ~300ms
            real._mic_frames = real._mic_frames[-frames_to_keep:]
            preserved_bytes = sum(len(f) for f in real._mic_frames)
            log.info("[BARGE-IN] Late speech detected (rms=%.0f), preserved %d frames (%d bytes, ~%.0fms)",
                     tail_rms, len(real._mic_frames), preserved_bytes, preserved_bytes / (48000 * 2) * 1000)
            await _log_barge_in_transcript(real._mic_frames)
            return True
    real._mic_frames.clear()
    log.info("[BARGE-IN] No speech detected during playback, cleared frames")
    return False


async def _log_barge_in_transcript(frames: list) -> None:
    """Debug helper: transcribe preserved barge-in frames and log the result."""
    try:
        pcm = b"".join(frames)
        text = await _transcribe_webrtc(pcm)
        if text and text.strip():
            log.info("[BARGE-IN] >>> Captured speech: %r <<<", text)
        else:
            log.info("[BARGE-IN] Transcription returned empty (frames may be too short or noisy)")
    except Exception as e:
        log.warning("[BARGE-IN] Debug transcription failed: %s", e)


async def _start_voice_loop(session: Session, scheduling_session) -> None:
    """Wire WebRTC audio through the scheduling FSM using energy-based VAD.

    Instead of the fragile text-stability callback (which requires Whisper
    to return identical text twice), this uses RMS energy to detect when
    the caller stops speaking, then transcribes the accumulated audio.
    This mirrors the proven Twilio approach in scheduling/app.py.
    """
    from scheduling.debug_events import get_broadcaster
    from scheduling.session import register_session, unregister_session

    sid = register_session(scheduling_session)
    scheduling_session.attach_broadcaster(get_broadcaster(sid))
    try:
        # 1. Generate and speak greeting
        greeting = await scheduling_session.get_greeting()
        log.info("WebRTC greeting: %s", greeting[:100])
        duration = await _speak(session, greeting)

        # 2. Enable mic frame capture directly (skip periodic transcription)
        real = session._real
        real._recording = True
        real._mic_frames.clear()
        log.info("Mic recording enabled, starting VAD voice loop")

        # 2b. Wait for greeting playback to finish before listening
        log.info("Greeting duration: %.1fs, waiting for playback...", duration)
        interrupted = await _wait_for_playback(real, duration, session)
        log.info("Greeting %s, now listening for user", "interrupted" if interrupted else "done")

        # 3. Wait for mic track to arrive (on_track fires async)
        for i in range(50):  # up to 5 seconds
            if real._mic_track is not None:
                break
            if i % 10 == 0:
                log.info("Waiting for mic track... (%d/5s)", i // 10)
            await asyncio.sleep(0.1)
        if real._mic_track is None:
            log.error("No mic track received from browser after 5s")
            return
        log.info("Mic track ready, listening for speech")

        # 4. VAD loop (mirrors Twilio approach in scheduling/app.py)
        # Settings are read each iteration so admin panel changes take effect live
        from scheduling.config import runtime_settings as _rs
        MIN_AUDIO = 9600           # minimum 0.1s of 48kHz int16
        MAX_BUFFER = 48000 * 2 * 30  # 30s cap

        silence_count = 0
        speech_confirm_count = 0   # consecutive frames above threshold
        has_speech = interrupted    # If user barged into greeting, their speech is in the buffer
        poll_count = 0
        no_frames_count = 0        # consecutive polls with 0 frames (dead connection detection)
        NO_FRAMES_LIMIT = 100      # 100 * 0.1s = 10s with no audio → connection dead
        DEAD_STATES = {"closed", "failed", "disconnected"}

        while not scheduling_session.is_done:
            await asyncio.sleep(0.1)
            poll_count += 1

            # Check if WebRTC peer connection is dead
            pc_state = getattr(real._pc, "connectionState", None) if real._pc else None
            if pc_state in DEAD_STATES:
                log.info("WebRTC connection state is '%s' — ending voice loop", pc_state)
                scheduling_session._done = True
                break

            # Periodic status log every 2s
            if poll_count % 20 == 0:
                n_frames = len(real._mic_frames)
                total_bytes = sum(len(f) for f in real._mic_frames) if n_frames else 0
                log.info(
                    "[VAD poll #%d] frames=%d bytes=%d has_speech=%s silence_count=%d recording=%s pc=%s",
                    poll_count, n_frames, total_bytes, has_speech, silence_count, real._recording, pc_state,
                )

            if not real._mic_frames:
                no_frames_count += 1
                if no_frames_count >= NO_FRAMES_LIMIT:
                    log.info("No audio frames for %ds — connection dead, ending voice loop", NO_FRAMES_LIMIT // 10)
                    scheduling_session._done = True
                    break
                continue
            no_frames_count = 0  # reset — we got frames

            # Read live-configurable thresholds each iteration
            # Normal VAD uses lower threshold — user is speaking directly,
            # no need to filter out echo/typing like barge-in does.
            threshold = _rs.get("vad_energy_threshold", 500)
            confirm_needed = _rs.get("vad_speech_confirm_frames", 2)
            silence_gap = _rs.get("vad_silence_gap", 15)

            # Check latest frame energy
            latest_frame = real._mic_frames[-1]
            rms = _compute_rms(latest_frame)

            # Log RMS every 5th poll (~500ms) for diagnostics
            if poll_count % 5 == 0:
                log.info(
                    "[VAD] rms=%.0f threshold=%d frames=%d has_speech=%s silence=%d",
                    rms, threshold, len(real._mic_frames), has_speech, silence_count,
                )

            if rms >= threshold:
                speech_confirm_count += 1
                if not has_speech and speech_confirm_count >= confirm_needed:
                    log.info(">>> [VAD] SPEECH START (rms=%.0f, confirmed %d frames) <<<", rms, speech_confirm_count)
                    has_speech = True
                silence_count = 0
            else:
                speech_confirm_count = 0  # reset — must be consecutive
                if has_speech and silence_count == 0:
                    log.info(">>> [VAD] SILENCE after speech (rms=%.0f) <<<", rms)
                silence_count += 1

            total = sum(len(f) for f in real._mic_frames)

            # Silence after speech → transcribe
            if has_speech and silence_count >= silence_gap and total > MIN_AUDIO:
                log.info(
                    "[VAD] Speech END — silence_count=%d, total_bytes=%d, transcribing...",
                    silence_count, total,
                )
                pcm = b"".join(real._mic_frames)
                real._mic_frames.clear()
                has_speech = False
                silence_count = 0
                speech_confirm_count = 0

                text = await _transcribe_webrtc(pcm)
                log.info("[VAD] Transcription result: %r", text)
                if text and text.strip():
                    log.info("Caller said: %s", text)
                    response = await scheduling_session.handle_utterance(text)
                    log.info(
                        "Response (step=%s): %s",
                        scheduling_session.current_step,
                        response[:100],
                    )
                    if response:
                        duration = await _speak(session, response)
                        log.info("Response duration: %.1fs, waiting for playback...", duration)
                        interrupted = await _wait_for_playback(real, duration, session)
                        if interrupted:
                            has_speech = True  # User is speaking — preserved frames in buffer
                            n_preserved = len(real._mic_frames)
                            preserved_bytes = sum(len(f) for f in real._mic_frames)
                            log.info("[VAD] Post-barge-in: has_speech=True, %d preserved frames (%d bytes), VAD will continue collecting",
                                     n_preserved, preserved_bytes)
                        else:
                            has_speech = False
                            log.info("[VAD] Playback finished normally, has_speech=False, waiting for new speech")
                        silence_count = 0
                        speech_confirm_count = 0

            # Prevent unbounded buffer growth
            elif total > MAX_BUFFER:
                log.warning("[VAD] Buffer overflow (%d bytes), clearing", total)
                real._mic_frames.clear()
                has_speech = False
                silence_count = 0
                speech_confirm_count = 0

    except Exception as e:
        log.error("Voice loop error: %s", e, exc_info=True)
    finally:
        unregister_session(sid)


async def handle_signaling_ws(ws: WebSocket) -> None:
    """Handle one WebRTC signaling WebSocket connection.

    Called from the FastAPI WebSocket endpoint.  Manages the full
    lifecycle: hello → ICE servers → SDP offer/answer → cleanup.
    """
    await ws.accept()
    log.info("Signaling WebSocket connected")

    session: Session | None = None
    sched_session = None
    voice_task: asyncio.Task | None = None
    ice_servers: list = []

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            msg_type = msg.get("type")
            log.debug("Signaling recv: %s", msg_type)

            if msg_type == "hello":
                # Fetch fresh TURN credentials, fall back to static config
                ice_servers = await fetch_twilio_turn_credentials()
                if not ice_servers:
                    ice_servers = get_fallback_ice_servers()

                await ws.send_json({
                    "type": "hello_ack",
                    "ice_servers": ice_servers,
                })
                log.info("Sent %d ICE servers to client", len(ice_servers))

            elif msg_type == "webrtc_offer":
                sdp = msg.get("sdp", "")
                if not sdp:
                    await ws.send_json({
                        "type": "error",
                        "message": "Missing SDP in webrtc_offer",
                    })
                    continue

                try:
                    # Create a new Session with the ICE servers we fetched
                    session = Session(ice_servers=ice_servers)
                    answer_sdp = await session.handle_offer(sdp)

                    await ws.send_json({
                        "type": "webrtc_answer",
                        "sdp": answer_sdp,
                    })
                    log.info("WebRTC session created, SDP answer sent")

                    # Start the voice conversation loop
                    try:
                        sched_session = _create_scheduling_session()
                        sched_session.start({"transport": "webrtc"})
                        voice_task = asyncio.ensure_future(
                            _start_voice_loop(session, sched_session)
                        )
                        log.info("Voice loop started")
                    except Exception as e:
                        log.error("Voice loop failed to start: %s", e)
                except ImportError as e:
                    log.error("WebRTC not available: %s", e)
                    await ws.send_json({
                        "type": "error",
                        "message": (
                            "WebRTC not available on server. "
                            "Install aiortc: pip install aiortc av"
                        ),
                    })
                except Exception as e:
                    log.error("WebRTC offer failed: %s", e)
                    await ws.send_json({
                        "type": "error",
                        "message": f"WebRTC setup failed: {e}",
                    })

            elif msg_type == "hangup":
                log.info("Client sent hangup")
                if sched_session:
                    sched_session._done = True
                if voice_task and not voice_task.done():
                    # Give the voice loop a moment to exit its poll gracefully
                    try:
                        await asyncio.wait_for(voice_task, timeout=1.0)
                    except asyncio.TimeoutError:
                        voice_task.cancel()
                    voice_task = None
                if session:
                    await session.close()
                    session = None
                    log.info("WebRTC session cleaned up after hangup")
                sched_session = None

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

            else:
                await ws.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        log.info("Signaling WebSocket disconnected")
    except Exception as e:
        log.error("Signaling error: %s", e)
    finally:
        if sched_session:
            sched_session._done = True
        if voice_task and not voice_task.done():
            voice_task.cancel()
        if session:
            await session.close()
            log.info("WebRTC session cleaned up")
