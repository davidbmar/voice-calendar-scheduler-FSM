"""E2E voice test harness — TTS/STT round-trip + session FSM + debug events.

Tests the full conversation pipeline:
  TTS synthesis → STT transcription → SchedulingSession FSM → debug event flow

Layers:
  - TTS/STT: Real Piper TTS + Faster-Whisper STT (in-process, no server)
  - LLM: Mocked — deterministic responses matching FSM parser expectations
  - Session: Real SchedulingSession with branching FSM logic
  - Voices: System = en_US-lessac-medium, Caller = en_US-hfc_female-medium
"""

import asyncio
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from unittest.mock import AsyncMock, patch

from scheduling.session import SchedulingSession
from scheduling.debug_events import DebugBroadcaster


# ── Voices ──────────────────────────────────────────────────────

SYSTEM_VOICE = "en_US-lessac-medium"
CALLER_VOICE = "en_US-hfc_female-medium"


# ── Response quality helper ─────────────────────────────────────

def assert_response_quality(text: str) -> None:
    """Assert that a response is suitable for TTS playback."""
    assert text, "Response must not be empty"
    assert "null" not in text.lower().split(), "Response must not contain 'null'"
    assert "PROGRESS" not in text, "Response must not contain PROGRESS tracking"
    assert "```json" not in text, "Response must not contain raw JSON blocks"
    assert "```" not in text, "Response must not contain code fences"


# ── TTS/STT fixtures (module-scoped, skip if models not available) ──

@pytest.fixture(scope="module")
def tts_synthesize():
    """Load TTS engine, skip if piper or models unavailable."""
    try:
        from engine.tts import synthesize
        # Quick smoke test to verify the model is downloaded
        audio = synthesize("test", SYSTEM_VOICE)
        if not audio:
            pytest.skip("TTS model produced no audio — model may not be downloaded")
        return synthesize
    except ImportError:
        pytest.skip("piper not installed")
    except Exception as e:
        pytest.skip(f"TTS unavailable: {e}")


@pytest.fixture(scope="module")
def stt_transcribe():
    """Load STT engine, skip if faster-whisper unavailable."""
    try:
        from engine.stt import transcribe
        return transcribe
    except ImportError:
        pytest.skip("faster-whisper not installed")
    except Exception as e:
        pytest.skip(f"STT unavailable: {e}")


# ── Mock LLM helpers ───────────────────────────────────────────

GREETING_RESPONSE = (
    "Hello! Welcome to Apartment Finders. "
    "I would love to help you find a great apartment today. "
    "What are you looking for?"
)

UTTERANCE_RESPONSE = (
    "Two bedrooms in downtown sounds wonderful! "
    "And a budget of two thousand a month, that gives us some great options. "
    "Let me search for apartments that match."
)

UTTERANCE_RESPONSE_WITH_JSON = (
    "Two bedrooms in downtown sounds wonderful! "
    "And a budget of two thousand a month, that gives us some great options. "
    "Let me search for apartments that match.\n"
    '```json\n{"bedrooms": 2, "budget": 2000, "area": "downtown", "intent": "gathered"}\n```'
)


def _make_mock_llm_generate_with_tools(response_text: str):
    """Create a mock that returns (text, []) for llm_generate_with_tools."""
    async def mock_fn(system, messages, tools, provider, model):
        return response_text, []
    return mock_fn


# ── Session + broadcaster helper ───────────────────────────────

def _make_wired_session():
    """Create a SchedulingSession with a DebugBroadcaster wired up."""
    session = SchedulingSession()
    broadcaster = DebugBroadcaster("e2e-test")
    queue = broadcaster.subscribe()
    session.attach_broadcaster(broadcaster)
    return session, broadcaster, queue


def _drain_events(queue):
    """Drain all events from a broadcaster queue."""
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


# ══════════════════════════════════════════════════════════════════
# Test Class 1: TTS/STT Round-Trip
# ══════════════════════════════════════════════════════════════════


class TestTTSRoundTrip:
    """Verify TTS and STT work correctly in isolation and together."""

    def test_system_voice_produces_audio(self, tts_synthesize):
        audio = tts_synthesize("Hello, welcome to our service.", SYSTEM_VOICE)
        assert isinstance(audio, bytes)
        assert len(audio) > 1000, "Audio should be substantial"

    def test_caller_voice_produces_audio(self, tts_synthesize):
        audio = tts_synthesize("I need a two bedroom apartment.", CALLER_VOICE)
        assert isinstance(audio, bytes)
        assert len(audio) > 1000

    def test_different_voices_produce_different_audio(self, tts_synthesize):
        text = "Hello there, how are you doing today?"
        audio_system = tts_synthesize(text, SYSTEM_VOICE)
        audio_caller = tts_synthesize(text, CALLER_VOICE)
        assert audio_system != audio_caller, "Different voices should produce different audio"

    def test_tts_stt_round_trip_fidelity(self, tts_synthesize, stt_transcribe):
        """TTS output fed through STT should produce similar text."""
        original = "I need two bedrooms downtown"
        audio = tts_synthesize(original, CALLER_VOICE)
        transcribed, no_speech_prob, avg_logprob = stt_transcribe(audio, 48000)

        # Basic fidelity: key words should survive the round-trip
        transcribed_lower = transcribed.lower()
        key_words = ["two", "bedroom", "downtown"]
        matches = sum(1 for w in key_words if w in transcribed_lower)
        similarity = matches / len(key_words)
        assert similarity > 0.5, (
            f"Round-trip fidelity too low ({similarity:.0%}): "
            f"'{original}' → '{transcribed}'"
        )


# ══════════════════════════════════════════════════════════════════
# Test Class 2: Greeting Flow
# ══════════════════════════════════════════════════════════════════


class TestGreetingFlow:
    """Session greeting through the voice pipeline."""

    @pytest.mark.asyncio
    async def test_greeting_is_coherent(self):
        """Greeting should be natural text, not null or PROGRESS."""
        session, _, _ = _make_wired_session()
        mock_fn = _make_mock_llm_generate_with_tools(GREETING_RESPONSE)

        with patch("engine.orchestrator.llm_generate_with_tools", side_effect=mock_fn):
            greeting = await session.get_greeting()

        assert_response_quality(greeting)
        assert len(greeting) > 10, "Greeting should be a real sentence"

    @pytest.mark.asyncio
    async def test_greeting_round_trips_through_tts_stt(self, tts_synthesize, stt_transcribe):
        """Greeting text → TTS → STT should preserve meaning."""
        session, _, _ = _make_wired_session()
        mock_fn = _make_mock_llm_generate_with_tools(GREETING_RESPONSE)

        with patch("engine.orchestrator.llm_generate_with_tools", side_effect=mock_fn):
            greeting = await session.get_greeting()

        audio = tts_synthesize(greeting, SYSTEM_VOICE)
        assert len(audio) > 1000

        transcribed, _, _ = stt_transcribe(audio, 48000)
        # At least some key words should survive
        assert any(
            w in transcribed.lower()
            for w in ["hello", "welcome", "apartment", "help"]
        ), f"Greeting lost meaning after TTS→STT: '{transcribed}'"


# ══════════════════════════════════════════════════════════════════
# Test Class 3: Utterance Handling
# ══════════════════════════════════════════════════════════════════


class TestUtteranceHandling:
    """Caller utterance processing through the session."""

    @pytest.mark.asyncio
    async def test_caller_text_gets_response(self):
        """A caller utterance should produce a non-empty response."""
        session, _, _ = _make_wired_session()
        mock_fn = _make_mock_llm_generate_with_tools(UTTERANCE_RESPONSE)

        with patch("engine.orchestrator.llm_generate_with_tools", side_effect=mock_fn):
            # First get greeting to initialize
            greeting_mock = _make_mock_llm_generate_with_tools(GREETING_RESPONSE)
            with patch("engine.orchestrator.llm_generate_with_tools", side_effect=greeting_mock):
                await session.get_greeting()

            response = await session.handle_utterance("I need two bedrooms downtown, budget about 2000")

        assert response, "Response must not be empty"
        assert len(response) > 10

    @pytest.mark.asyncio
    async def test_response_passes_quality_checks(self):
        """Response should be clean spoken text."""
        session, _, _ = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(UTTERANCE_RESPONSE)):
            response = await session.handle_utterance("I need two bedrooms downtown")

        assert_response_quality(response)


# ══════════════════════════════════════════════════════════════════
# Test Class 4: Debug Event Flow
# ══════════════════════════════════════════════════════════════════


class TestDebugEventFlow:
    """Debug events should be emitted during conversation."""

    @pytest.mark.asyncio
    async def test_events_emitted_during_greeting(self):
        """Greeting should emit llm_call and llm_response events."""
        session, broadcaster, queue = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        events = _drain_events(queue)
        event_types = [e["type"] for e in events]
        assert "llm_call" in event_types, f"Expected llm_call, got: {event_types}"
        assert "llm_response" in event_types, f"Expected llm_response, got: {event_types}"

    @pytest.mark.asyncio
    async def test_events_emitted_during_utterance(self):
        """Utterance handling should emit stt, llm_call, llm_response events."""
        session, broadcaster, queue = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        # Drain greeting events
        _drain_events(queue)

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(UTTERANCE_RESPONSE)):
            await session.handle_utterance("I need two bedrooms")

        events = _drain_events(queue)
        event_types = [e["type"] for e in events]
        assert "stt" in event_types, f"Expected stt event, got: {event_types}"
        assert "llm_call" in event_types, f"Expected llm_call, got: {event_types}"
        assert "llm_response" in event_types, f"Expected llm_response, got: {event_types}"

    @pytest.mark.asyncio
    async def test_field_progress_events_detect_mentioned_fields(self):
        """field_progress events should fire when fields are discussed."""
        session, broadcaster, queue = _make_wired_session()

        # get_greeting() doesn't process JSON signals or trigger transitions.
        # We must use handle_utterance() to advance from hello → greet_and_gather.
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        # Transition via handle_utterance with a "greeted" JSON signal
        transition_response = (
            "Great to meet you!\n"
            '```json\n{"intent": "greeted"}\n```'
        )
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(transition_response)):
            await session.handle_utterance("Hi there")

        assert session.current_step == "greet_and_gather", (
            f"Expected greet_and_gather, got {session.current_step}"
        )

        _drain_events(queue)

        # Now handle utterance mentioning bedrooms and budget
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(UTTERANCE_RESPONSE)):
            await session.handle_utterance("I need two bedrooms, budget about 2000")

        events = _drain_events(queue)
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) >= 1, f"Expected field_progress events, got types: {[e['type'] for e in events]}"

        detected_fields = field_events[0]["data"]["fields"]
        assert "bedrooms" in detected_fields
        assert "budget" in detected_fields


# ══════════════════════════════════════════════════════════════════
# Test Class 5: State Transitions
# ══════════════════════════════════════════════════════════════════


class TestStateTransitions:
    """FSM state progression through the conversation."""

    def test_initial_state_is_hello(self):
        """Session should start in the 'hello' state."""
        session = SchedulingSession()
        assert session.current_step == "hello"

    @pytest.mark.asyncio
    async def test_transitions_to_greet_and_gather(self):
        """After utterance with 'greeted' intent, should advance to greet_and_gather."""
        session, _, queue = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        # Transition happens via handle_utterance, not get_greeting
        transition_response = (
            "Great to meet you!\n"
            '```json\n{"intent": "greeted"}\n```'
        )
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(transition_response)):
            await session.handle_utterance("Hi there")

        assert session.current_step == "greet_and_gather"

        events = _drain_events(queue)
        transition_events = [e for e in events if e["type"] == "transition"]
        assert len(transition_events) == 1
        assert transition_events[0]["data"]["from"] == "hello"
        assert transition_events[0]["data"]["to"] == "greet_and_gather"

    @pytest.mark.asyncio
    async def test_wildcard_transition_from_hello(self):
        """The hello state's wildcard '*' transition also routes to greet_and_gather."""
        session, _, _ = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        # Use a different intent that triggers the wildcard via handle_utterance
        wildcard_response = (
            "Hello!\n"
            '```json\n{"intent": "anything"}\n```'
        )
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(wildcard_response)):
            await session.handle_utterance("Hi")

        assert session.current_step == "greet_and_gather"

    @pytest.mark.asyncio
    async def test_stays_in_state_without_json_signal(self):
        """Without a JSON completion signal, session stays in current state."""
        session, _, _ = _make_wired_session()

        # Greeting without JSON — stays in hello
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        assert session.current_step == "hello"

    @pytest.mark.asyncio
    async def test_session_not_done_mid_conversation(self):
        """Session should not be done after just greeting."""
        session, _, _ = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        assert not session.is_done


# ══════════════════════════════════════════════════════════════════
# Test Class 6: Response Quality
# ══════════════════════════════════════════════════════════════════


class TestResponseQuality:
    """Responses should be clean spoken text — no null, no PROGRESS, no raw JSON."""

    @pytest.mark.asyncio
    async def test_greeting_quality(self):
        session, _, _ = _make_wired_session()
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            greeting = await session.get_greeting()
        assert_response_quality(greeting)

    @pytest.mark.asyncio
    async def test_utterance_response_quality(self):
        session, _, _ = _make_wired_session()
        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(UTTERANCE_RESPONSE)):
            response = await session.handle_utterance("I need two bedrooms")
        assert_response_quality(response)

    @pytest.mark.asyncio
    async def test_response_with_json_signal_strips_json(self):
        """When LLM includes a JSON signal, the spoken text should be clean."""
        session, _, _ = _make_wired_session()

        # Use a JSON signal with an intent that doesn't match any transition
        # in the hello state, so the FSM stays put and we test JSON stripping
        # without triggering tool step execution.
        response_with_json = (
            "Two bedrooms in downtown sounds wonderful! "
            "And a budget of two thousand a month, great options.\n"
            '```json\n{"bedrooms": 2, "budget": 2000, "intent": "partial"}\n```'
        )

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            await session.get_greeting()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(response_with_json)):
            response = await session.handle_utterance(
                "I need two bedrooms downtown, budget about 2000"
            )

        assert_response_quality(response)
        assert "bedrooms" in response.lower() or "downtown" in response.lower()

    @pytest.mark.asyncio
    async def test_multi_turn_quality(self):
        """Multiple turns should all produce quality responses."""
        session, _, _ = _make_wired_session()

        with patch("engine.orchestrator.llm_generate_with_tools",
                    side_effect=_make_mock_llm_generate_with_tools(GREETING_RESPONSE)):
            greeting = await session.get_greeting()
        assert_response_quality(greeting)

        turns = [
            ("Hi there", "Great to hear from you! What kind of apartment are you looking for?"),
            ("Two bedrooms", "Two bedrooms, got it! Any preferred area or budget?"),
            ("Downtown, about 2000", UTTERANCE_RESPONSE),
        ]
        for caller_text, llm_reply in turns:
            with patch("engine.orchestrator.llm_generate_with_tools",
                        side_effect=_make_mock_llm_generate_with_tools(llm_reply)):
                response = await session.handle_utterance(caller_text)
            assert_response_quality(response)
