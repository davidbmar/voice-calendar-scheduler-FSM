"""WebRTCChannel — VoiceChannel adapter wrapping the engine's Session class.

The engine-repo's ``gateway.webrtc.Session`` already manages:
  - RTCPeerConnection lifecycle (offer/answer, ICE)
  - Mic audio capture (48kHz int16 → buffer of PCM bytes)
  - TTS playback via speak_text()

This adapter exposes that functionality through the ``VoiceChannel`` ABC
so the scheduling layer can treat phone calls (Twilio) and browser calls
(WebRTC) identically.

Audio format note:
  The Session captures mic audio at 48kHz (WebRTC native).  This channel
  resamples to 16kHz for the STT pipeline and accepts 16kHz from TTS,
  upsampling to 48kHz for WebRTC playback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

import numpy as np

from scheduling.channels.base import AudioFrame, VoiceChannel

log = logging.getLogger("webrtc_channel")

WEBRTC_SAMPLE_RATE = 48000
TARGET_SAMPLE_RATE = 16000
DOWNSAMPLE_FACTOR = WEBRTC_SAMPLE_RATE // TARGET_SAMPLE_RATE  # 3
FRAME_DURATION_MS = 20
FRAME_SAMPLES_48K = WEBRTC_SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 960
FRAME_SAMPLES_16K = TARGET_SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 320


def _resample_48k_to_16k(pcm_48k: bytes) -> bytes:
    """Downsample PCM int16 from 48kHz to 16kHz (take every 3rd sample)."""
    samples = np.frombuffer(pcm_48k, dtype=np.int16)
    downsampled = samples[::DOWNSAMPLE_FACTOR]
    return downsampled.tobytes()


def _resample_16k_to_48k(pcm_16k: bytes) -> bytes:
    """Upsample PCM int16 from 16kHz to 48kHz (linear interpolation)."""
    samples_16k = np.frombuffer(pcm_16k, dtype=np.int16).astype(np.float32)
    num_output = len(samples_16k) * DOWNSAMPLE_FACTOR
    indices = np.linspace(0, len(samples_16k) - 1, num_output)
    samples_48k = np.interp(indices, np.arange(len(samples_16k)), samples_16k)
    return samples_48k.astype(np.int16).tobytes()


class WebRTCChannel(VoiceChannel):
    """VoiceChannel adapter wrapping the engine's WebRTC Session.

    The Session object must already have been created and had its SDP
    offer/answer exchange completed before wrapping in this channel.

    Usage::

        from gateway.webrtc import Session
        session = Session(ice_servers=ice_servers)
        answer_sdp = await session.handle_offer(offer_sdp)

        channel = WebRTCChannel(session, session_id="abc123")
        async for frame in channel.receive_audio():
            # frame is PCM 16kHz mono
            ...
    """

    def __init__(
        self,
        session: Any,  # gateway.webrtc.Session — 'Any' to avoid hard import
        session_id: str = "",
        user_agent: str = "",
    ):
        self._session = session
        self._session_id = session_id
        self._user_agent = user_agent
        self._closed = False

    async def receive_audio(self) -> AsyncIterator[AudioFrame]:
        """Yield PCM 16kHz frames from the WebRTC mic track.

        Starts recording on the underlying Session and polls the
        accumulated mic frames, resampling from 48kHz to 16kHz.
        The generator runs until close() is called.
        """
        # Start mic recording on the session
        self._session.start_recording()
        log.info("WebRTC mic recording started (session=%s)", self._session_id)

        try:
            while not self._closed:
                # Poll for accumulated mic frames
                # The Session stores raw 48kHz PCM chunks in _mic_frames
                await asyncio.sleep(FRAME_DURATION_MS / 1000)

                if not self._session._mic_frames:
                    continue

                # Drain all available frames
                frames_48k = list(self._session._mic_frames)
                self._session._mic_frames.clear()

                for pcm_48k in frames_48k:
                    pcm_16k = _resample_48k_to_16k(pcm_48k)
                    yield AudioFrame(samples=pcm_16k)

        except asyncio.CancelledError:
            log.info("receive_audio cancelled (session=%s)", self._session_id)
        finally:
            # Stop recording but don't do final transcription (we handle that)
            self._session._recording = False
            log.info("WebRTC mic recording stopped (session=%s)", self._session_id)

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send PCM 16kHz audio to the WebRTC peer.

        Upsamples to 48kHz and enqueues into the Session's audio queue
        for playback through the WebRTC audio track.
        """
        for frame in frames:
            pcm_48k = _resample_16k_to_48k(frame.samples)
            self._session._audio_queue.enqueue(pcm_48k)

        # Ensure the TTS generator is attached to the audio source
        self._session._audio_source.set_generator(self._session._tts_generator)

    async def get_caller_info(self) -> dict[str, Any]:
        """Return WebRTC session metadata."""
        return {
            "session_id": self._session_id,
            "user_agent": self._user_agent,
            "transport": "webrtc",
        }

    async def close(self) -> None:
        """Close the WebRTC channel and underlying session."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._session.close()
        except Exception:
            pass  # Session may already be closed
        log.info("WebRTC channel closed (session=%s)", self._session_id)
