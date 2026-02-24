"""TwilioMediaStreamChannel — VoiceChannel for Twilio Media Streams.

Twilio Media Streams deliver audio over a WebSocket as base64-encoded
mulaw (G.711 u-law) at 8kHz mono.  This channel:

  inbound:  mulaw 8kHz → PCM 16kHz (upsample 2x for STT)
  outbound: PCM 16kHz → mulaw 8kHz (downsample 2x for Twilio)

Protocol reference:
  https://www.twilio.com/docs/voice/media-streams/websocket-messages

WebSocket message flow:
  ← {"event":"connected", "protocol":"Call", "version":"1.0.0"}
  ← {"event":"start",     "start":{"streamSid":"...","callSid":"...","from":"+1..."}}
  ← {"event":"media",     "media":{"payload":"<base64 mulaw>","timestamp":"..."}}
  ← {"event":"stop"}

  → {"event":"media", "streamSid":"...", "media":{"payload":"<base64 mulaw>"}}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, AsyncIterator

import numpy as np

try:
    import audioop
except ImportError:
    # Python 3.13+ removed audioop from stdlib
    import audioop_lts as audioop  # type: ignore[no-redef]

from fastapi import WebSocket

from scheduling.channels.base import AudioFrame, VoiceChannel

log = logging.getLogger("twilio_channel")

# Twilio sends mulaw 8kHz; STT expects PCM 16kHz
TWILIO_SAMPLE_RATE = 8000
TARGET_SAMPLE_RATE = 16000
UPSAMPLE_FACTOR = TARGET_SAMPLE_RATE // TWILIO_SAMPLE_RATE  # 2


def _mulaw_to_pcm16k(mulaw_bytes: bytes) -> bytes:
    """Convert mulaw 8kHz → PCM int16 16kHz.

    Steps:
      1. audioop.ulaw2lin: mulaw → PCM int16 @ 8kHz
      2. numpy resample: 8kHz → 16kHz (linear interpolation)
    """
    # mulaw → PCM 16-bit signed LE at 8kHz
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)  # 2 = sample width in bytes

    # Upsample 8kHz → 16kHz via linear interpolation
    samples_8k = np.frombuffer(pcm_8k, dtype=np.int16).astype(np.float32)
    num_output = len(samples_8k) * UPSAMPLE_FACTOR
    indices = np.linspace(0, len(samples_8k) - 1, num_output)
    samples_16k = np.interp(indices, np.arange(len(samples_8k)), samples_8k)
    return samples_16k.astype(np.int16).tobytes()


def _pcm16k_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert PCM int16 16kHz → mulaw 8kHz.

    Steps:
      1. numpy resample: 16kHz → 8kHz (decimation)
      2. audioop.lin2ulaw: PCM int16 → mulaw
    """
    # Downsample 16kHz → 8kHz (take every other sample)
    samples_16k = np.frombuffer(pcm_bytes, dtype=np.int16)
    samples_8k = samples_16k[::UPSAMPLE_FACTOR]
    pcm_8k = samples_8k.tobytes()

    # PCM → mulaw
    return audioop.lin2ulaw(pcm_8k, 2)  # 2 = sample width in bytes


class TwilioMediaStreamChannel(VoiceChannel):
    """VoiceChannel implementation for Twilio Media Streams over WebSocket.

    Usage::

        @app.websocket("/twilio/stream")
        async def twilio_stream(ws: WebSocket):
            await ws.accept()
            channel = TwilioMediaStreamChannel(ws)
            await channel.initialize()  # wait for 'start' event

            async for frame in channel.receive_audio():
                # frame is PCM 16kHz mono
                text = await stt.transcribe(frame.samples, frame.sample_rate)
                ...
    """

    def __init__(self, websocket: WebSocket):
        self._ws = websocket
        self._stream_sid: str = ""
        self._call_sid: str = ""
        self._caller_number: str = ""
        self._start_metadata: dict[str, Any] = {}
        self._connected = False
        self._stopped = False

    async def initialize(self) -> None:
        """Wait for the Twilio 'connected' and 'start' events.

        Must be called after the WebSocket is accepted and before
        calling receive_audio().  Populates stream/call SID and
        caller metadata.
        """
        while not self._stream_sid:
            raw = await self._ws.receive_text()
            msg = json.loads(raw)
            event = msg.get("event")

            if event == "connected":
                log.info(
                    "Twilio connected: protocol=%s version=%s",
                    msg.get("protocol"),
                    msg.get("version"),
                )
                self._connected = True

            elif event == "start":
                start = msg.get("start", {})
                self._stream_sid = start.get("streamSid", "")
                self._call_sid = start.get("callSid", "")
                self._caller_number = start.get("from", "")
                self._start_metadata = start
                log.info(
                    "Twilio stream started: stream_sid=%s call_sid=%s from=%s",
                    self._stream_sid,
                    self._call_sid,
                    self._caller_number,
                )

    async def receive_audio(self) -> AsyncIterator[AudioFrame]:
        """Yield PCM 16kHz frames from Twilio's mulaw 8kHz media stream.

        Runs until the 'stop' event is received or the WebSocket closes.
        """
        try:
            while not self._stopped:
                try:
                    raw = await self._ws.receive_text()
                except Exception:
                    log.info("Twilio WebSocket closed")
                    break

                msg = json.loads(raw)
                event = msg.get("event")

                if event == "media":
                    payload_b64 = msg["media"]["payload"]
                    mulaw_bytes = base64.b64decode(payload_b64)
                    pcm_16k = _mulaw_to_pcm16k(mulaw_bytes)
                    yield AudioFrame(samples=pcm_16k)

                elif event == "stop":
                    log.info("Twilio stream stopped")
                    self._stopped = True
                    break

                # Ignore other events (mark, dtmf, etc.)
        except asyncio.CancelledError:
            log.info("receive_audio cancelled")

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send PCM 16kHz frames back to Twilio as mulaw 8kHz.

        Each frame is converted and sent as a separate media message.
        """
        if not self._stream_sid:
            log.warning("Cannot send audio: stream not initialized")
            return

        for frame in frames:
            mulaw_bytes = _pcm16k_to_mulaw(frame.samples)
            payload_b64 = base64.b64encode(mulaw_bytes).decode("ascii")

            message = {
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {"payload": payload_b64},
            }
            try:
                await self._ws.send_text(json.dumps(message))
            except Exception:
                log.warning("Failed to send audio to Twilio")
                break

    async def get_caller_info(self) -> dict[str, Any]:
        """Return Twilio call metadata."""
        return {
            "phone_number": self._caller_number,
            "call_sid": self._call_sid,
            "stream_sid": self._stream_sid,
            "transport": "twilio",
            "metadata": self._start_metadata,
        }

    async def close(self) -> None:
        """Close the Twilio Media Stream WebSocket."""
        self._stopped = True
        try:
            await self._ws.close()
        except Exception:
            pass  # Already closed
        log.info("Twilio channel closed (call_sid=%s)", self._call_sid)
