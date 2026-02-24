"""Tests for TwilioMediaStreamChannel — mulaw ↔ PCM conversion."""

import base64
import json
import struct
from unittest.mock import AsyncMock

import numpy as np
import pytest

# We need audioop for mulaw conversion
try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore[no-redef]

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.channels.base import AudioFrame
from scheduling.channels.twilio_channel import (
    TwilioMediaStreamChannel,
    _mulaw_to_pcm16k,
    _pcm16k_to_mulaw,
)


# ── Audio conversion tests ──────────────────────────────────────────


class TestMulawConversion:
    def test_mulaw_to_pcm16k_output_rate(self):
        """mulaw 8kHz → PCM 16kHz should double the sample count."""
        # Generate 160 samples of mulaw (20ms at 8kHz)
        pcm_8k = struct.pack("<" + "h" * 160, *([1000] * 160))
        mulaw = audioop.lin2ulaw(pcm_8k, 2)

        pcm_16k = _mulaw_to_pcm16k(mulaw)

        # 160 mulaw samples → 320 PCM samples (2 bytes each)
        assert len(pcm_16k) == 160 * 2 * 2  # doubled samples * 2 bytes

    def test_pcm16k_to_mulaw_output_rate(self):
        """PCM 16kHz → mulaw 8kHz should halve the sample count."""
        # 320 samples of PCM 16kHz (20ms)
        pcm_16k = struct.pack("<" + "h" * 320, *([1000] * 320))

        mulaw = _pcm16k_to_mulaw(pcm_16k)

        # 320 PCM samples → 160 mulaw bytes
        assert len(mulaw) == 160

    def test_roundtrip_preserves_shape(self):
        """mulaw → PCM 16k → mulaw should roughly preserve the signal."""
        # Create a simple sine wave at 8kHz
        t = np.arange(160) / 8000.0
        sine = (np.sin(2 * np.pi * 400 * t) * 10000).astype(np.int16)
        pcm_8k = sine.tobytes()
        mulaw_orig = audioop.lin2ulaw(pcm_8k, 2)

        # Round-trip
        pcm_16k = _mulaw_to_pcm16k(mulaw_orig)
        mulaw_back = _pcm16k_to_mulaw(pcm_16k)

        # Length should match
        assert len(mulaw_back) == len(mulaw_orig)

    def test_silence_roundtrip(self):
        """Silence (zeros) should remain near-silent through conversion."""
        silence_mulaw = audioop.lin2ulaw(b"\x00\x00" * 160, 2)
        pcm_16k = _mulaw_to_pcm16k(silence_mulaw)
        samples = np.frombuffer(pcm_16k, dtype=np.int16)
        # Should be very low amplitude
        assert np.abs(samples).max() < 200


# ── Channel protocol tests ──────────────────────────────────────────


class TestTwilioChannel:
    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_initialize_extracts_metadata(self, mock_ws):
        """initialize() should parse connected + start events."""
        messages = [
            json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}),
            json.dumps({
                "event": "start",
                "start": {
                    "streamSid": "MZ123",
                    "callSid": "CA456",
                    "from": "+15551234567",
                },
            }),
        ]
        mock_ws.receive_text = AsyncMock(side_effect=messages)

        channel = TwilioMediaStreamChannel(mock_ws)
        await channel.initialize()

        info = await channel.get_caller_info()
        assert info["phone_number"] == "+15551234567"
        assert info["call_sid"] == "CA456"
        assert info["stream_sid"] == "MZ123"
        assert info["transport"] == "twilio"

    @pytest.mark.asyncio
    async def test_receive_audio_yields_pcm_frames(self, mock_ws):
        """receive_audio should yield PCM 16kHz AudioFrames from mulaw."""
        # Create a mulaw payload (160 bytes = 20ms at 8kHz)
        pcm_8k = b"\x00\x00" * 160
        mulaw = audioop.lin2ulaw(pcm_8k, 2)
        b64_payload = base64.b64encode(mulaw).decode("ascii")

        messages = [
            json.dumps({
                "event": "media",
                "media": {"payload": b64_payload, "timestamp": "0"},
            }),
            json.dumps({"event": "stop"}),
        ]
        mock_ws.receive_text = AsyncMock(side_effect=messages)

        channel = TwilioMediaStreamChannel(mock_ws)
        channel._stream_sid = "MZ123"  # skip initialize

        frames = []
        async for frame in channel.receive_audio():
            frames.append(frame)

        assert len(frames) == 1
        assert isinstance(frames[0], AudioFrame)
        assert frames[0].sample_rate == 16000
        # 160 mulaw samples → 320 PCM int16 samples = 640 bytes
        assert len(frames[0].samples) == 640

    @pytest.mark.asyncio
    async def test_send_audio_encodes_mulaw(self, mock_ws):
        """send_audio should convert PCM 16kHz to base64 mulaw."""
        channel = TwilioMediaStreamChannel(mock_ws)
        channel._stream_sid = "MZ123"
        mock_ws.send_text = AsyncMock()

        # 320 PCM samples at 16kHz = 20ms
        pcm_16k = b"\x00\x00" * 320
        frame = AudioFrame(samples=pcm_16k)
        await channel.send_audio([frame])

        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["event"] == "media"
        assert sent["streamSid"] == "MZ123"
        # Verify the payload is valid base64
        payload = base64.b64decode(sent["media"]["payload"])
        assert len(payload) == 160  # 320 PCM / 2 = 160 mulaw bytes

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, mock_ws):
        """close() should be safe to call multiple times."""
        channel = TwilioMediaStreamChannel(mock_ws)
        await channel.close()
        await channel.close()
        # Should not raise


# ── AudioFrame tests ────────────────────────────────────────────────


class TestAudioFrame:
    def test_duration_ms(self):
        """20ms of 16kHz audio = 320 samples = 640 bytes."""
        frame = AudioFrame(samples=b"\x00\x00" * 320)
        assert frame.duration_ms == pytest.approx(20.0)

    def test_num_samples(self):
        frame = AudioFrame(samples=b"\x00\x00" * 100)
        assert frame.num_samples == 100
