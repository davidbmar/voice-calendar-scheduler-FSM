"""Tests for WebRTCChannel adapter."""

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.channels.base import AudioFrame, VoiceChannel


class TestVoiceChannelABC:
    def test_cannot_instantiate(self):
        """VoiceChannel is abstract — can't be instantiated directly."""
        with pytest.raises(TypeError):
            VoiceChannel()

    def test_audioframe_properties(self):
        """AudioFrame should correctly compute duration and sample count."""
        # 160 samples of int16 = 320 bytes = 10ms at 16kHz
        frame = AudioFrame(samples=b"\x00\x00" * 160)
        assert frame.num_samples == 160
        assert frame.duration_ms == pytest.approx(10.0)
        assert frame.sample_rate == 16000
        assert frame.channels == 1

    def test_concrete_channel(self):
        """A concrete subclass must implement all abstract methods."""
        class DummyChannel(VoiceChannel):
            async def receive_audio(self):
                yield AudioFrame(samples=b"\x00\x00" * 320)

            async def send_audio(self, frames):
                pass

            async def get_caller_info(self):
                return {"transport": "dummy"}

            async def close(self):
                pass

        channel = DummyChannel()
        assert isinstance(channel, VoiceChannel)
