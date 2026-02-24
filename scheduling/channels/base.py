"""VoiceChannel ABC — normalizes different audio transports to PCM 16kHz.

Different voice channels (Twilio mulaw 8kHz, WebRTC Opus 48kHz) deliver
audio in different formats.  The VoiceChannel interface lets the rest of
the scheduling stack work exclusively with PCM 16kHz mono — the format
expected by the STT engine (faster-whisper).

Implementors handle the format conversion in both directions:
  inbound:  native format → PCM 16kHz (for STT)
  outbound: PCM 16kHz → native format (for the caller)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass
class AudioFrame:
    """Normalized audio frame: PCM 16kHz mono int16 little-endian."""

    samples: bytes  # int16 LE PCM
    sample_rate: int = 16000
    channels: int = 1

    @property
    def duration_ms(self) -> float:
        """Duration of this frame in milliseconds."""
        num_samples = len(self.samples) // 2  # 2 bytes per int16 sample
        return (num_samples / self.sample_rate) * 1000

    @property
    def num_samples(self) -> int:
        """Number of int16 samples in this frame."""
        return len(self.samples) // 2


class VoiceChannel(ABC):
    """Abstract voice channel — normalizes audio from any transport.

    Each concrete channel wraps a specific transport (Twilio WebSocket,
    WebRTC peer connection, etc.) and translates between that transport's
    native audio format and the canonical PCM 16kHz mono format used by
    the STT/TTS pipeline.
    """

    @abstractmethod
    async def receive_audio(self) -> AsyncIterator[AudioFrame]:
        """Yield normalized PCM 16kHz audio frames from the caller.

        This is an async generator that runs for the lifetime of the call.
        Each yielded AudioFrame contains PCM 16kHz mono int16 LE data,
        regardless of the underlying transport format.
        """

    @abstractmethod
    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send PCM 16kHz audio frames back to the caller.

        The channel converts from PCM 16kHz to its native format
        (mulaw 8kHz for Twilio, Opus 48kHz for WebRTC, etc.) before
        transmitting.
        """

    @abstractmethod
    async def get_caller_info(self) -> dict[str, Any]:
        """Return caller metadata.

        Keys vary by transport but may include:
          - phone_number: E.164 phone number (Twilio)
          - call_sid: Twilio Call SID
          - stream_sid: Twilio Media Stream SID
          - session_id: WebRTC session identifier
          - user_agent: browser User-Agent string (WebRTC)
        """

    @abstractmethod
    async def close(self) -> None:
        """Tear down the channel connection.

        Releases transport resources (WebSocket, peer connection, etc.).
        Safe to call multiple times.
        """
