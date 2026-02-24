"""Tests for debug event tracing — ensures events flow from session to broadcaster.

These tests verify that:
1. DebugBroadcaster emits events to subscribers
2. SchedulingSession emits the right events at the right times
3. attach_broadcaster wires the session to the broadcaster
4. Field progress detection works correctly
5. Events contain the expected data structure
"""

import asyncio
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.debug_events import DebugBroadcaster, DebugEvent, get_broadcaster, remove_broadcaster
from scheduling.session import SchedulingSession, register_session, unregister_session


# ── DebugBroadcaster unit tests ─────────────────────────────────────


class TestDebugBroadcaster:
    def test_emit_without_subscribers(self):
        """Emitting with no subscribers should not raise."""
        b = DebugBroadcaster("test-1")
        b.emit("transition", "hello", {"from": "idle", "to": "hello"})
        assert len(b.event_log) == 1

    def test_emit_to_subscriber(self):
        b = DebugBroadcaster("test-2")
        q = b.subscribe()
        b.emit("stt", "greet_and_gather", {"text": "hello"})

        assert not q.empty()
        event = q.get_nowait()
        assert event["type"] == "stt"
        assert event["state_id"] == "greet_and_gather"
        assert event["data"]["text"] == "hello"
        assert event["session_id"] == "test-2"
        assert "timestamp" in event

    def test_multiple_subscribers(self):
        b = DebugBroadcaster("test-3")
        q1 = b.subscribe()
        q2 = b.subscribe()
        b.emit("transition", "hello", {"from": "idle", "to": "hello"})

        assert not q1.empty()
        assert not q2.empty()
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1["type"] == e2["type"] == "transition"

    def test_unsubscribe(self):
        b = DebugBroadcaster("test-4")
        q = b.subscribe()
        assert b.subscriber_count == 1
        b.unsubscribe(q)
        assert b.subscriber_count == 0

        # Emitting after unsubscribe should not put anything in the queue
        b.emit("stt", "hello", {"text": "test"})
        assert q.empty()

    def test_event_log_accumulates(self):
        b = DebugBroadcaster("test-5")
        b.emit("stt", "hello", {"text": "hi"})
        b.emit("llm_response", "hello", {"response": "Hello!"})
        b.emit("transition", "hello", {"from": "hello", "to": "greet_and_gather"})

        log = b.event_log
        assert len(log) == 3
        assert [e["type"] for e in log] == ["stt", "llm_response", "transition"]

    def test_event_log_returns_copy(self):
        b = DebugBroadcaster("test-6")
        b.emit("stt", "hello", {"text": "hi"})
        log1 = b.event_log
        log1.clear()
        # Original should be untouched
        assert len(b.event_log) == 1

    def test_queue_overflow_drops_oldest(self):
        """When the queue is full, oldest events should be dropped."""
        b = DebugBroadcaster("test-7")
        q = b.subscribe()
        # Fill the queue (maxsize=200)
        for i in range(200):
            b.emit("stt", "hello", {"text": f"msg-{i}"})
        assert q.full()

        # Next emit should succeed (drops oldest)
        b.emit("stt", "hello", {"text": "msg-200"})
        # Queue should still be full but first item is now msg-1 (msg-0 was dropped)
        first = q.get_nowait()
        assert first["data"]["text"] == "msg-1"


# ── Broadcaster registry tests ──────────────────────────────────────


class TestBroadcasterRegistry:
    def test_get_broadcaster_creates_new(self):
        b = get_broadcaster("registry-test-1")
        assert isinstance(b, DebugBroadcaster)

    def test_get_broadcaster_returns_same(self):
        b1 = get_broadcaster("registry-test-2")
        b2 = get_broadcaster("registry-test-2")
        assert b1 is b2

    def test_remove_broadcaster(self):
        b1 = get_broadcaster("registry-test-3")
        remove_broadcaster("registry-test-3")
        b2 = get_broadcaster("registry-test-3")
        assert b1 is not b2


# ── Session + Broadcaster integration tests ─────────────────────────


class TestSessionBroadcasterWiring:
    def test_no_broadcaster_no_error(self):
        """Session without a broadcaster should emit events silently."""
        session = SchedulingSession()
        # _emit_event should not raise when no broadcaster is attached
        session._emit_event("stt", {"text": "hello"})

    def test_attach_broadcaster(self):
        session = SchedulingSession()
        b = DebugBroadcaster("wire-test-1")
        q = b.subscribe()

        session.attach_broadcaster(b)
        session._emit_event("stt", {"text": "hello"})

        assert not q.empty()
        event = q.get_nowait()
        assert event["type"] == "stt"
        assert event["data"]["text"] == "hello"

    def test_register_session_and_attach(self):
        """Full wiring: register session → get broadcaster → attach."""
        session = SchedulingSession()
        sid = register_session(session)

        b = get_broadcaster(sid)
        session.attach_broadcaster(b)
        q = b.subscribe()

        session._emit_event("llm_call", {"user_text": "test", "system_prompt": "..."})

        event = q.get_nowait()
        assert event["type"] == "llm_call"
        assert event["session_id"] == sid

        unregister_session(sid)

    def test_emit_event_includes_current_step(self):
        """Events should include the current step ID as state_id."""
        session = SchedulingSession()
        b = DebugBroadcaster("step-test")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("stt", {"text": "hi"})

        event = q.get_nowait()
        # The initial step should be the workflow's initial_state
        assert event["state_id"] == session._current_step_id

    def test_session_snapshot_includes_event_log(self):
        """Session snapshot should include event_log when broadcaster is attached."""
        session = SchedulingSession()
        b = DebugBroadcaster("snap-test")
        session.attach_broadcaster(b)

        session._emit_event("stt", {"text": "hello"})
        session._emit_event("llm_response", {"response": "Hi there!"})

        snapshot = session.to_dict(detail=True)
        assert "event_log" in snapshot
        assert len(snapshot["event_log"]) == 2


# ── Field progress detection tests ──────────────────────────────────


class TestFieldProgressDetection:
    def _make_session_with_broadcaster(self):
        session = SchedulingSession()
        b = DebugBroadcaster("field-test")
        q = b.subscribe()
        session.attach_broadcaster(b)
        return session, q

    def test_detects_bedrooms_mention(self):
        session, q = self._make_session_with_broadcaster()
        from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
        state = WORKFLOW_DEF.states["greet_and_gather"]

        session._detect_field_progress(state, "I need 2 bedrooms", "Two bedrooms, got it!")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) == 1
        assert "bedrooms" in field_events[0]["data"]["fields"]

    def test_detects_budget_mention(self):
        session, q = self._make_session_with_broadcaster()
        from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
        state = WORKFLOW_DEF.states["greet_and_gather"]

        session._detect_field_progress(state, "my budget is 2000", "Two thousand a month, noted!")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) == 1
        assert "budget" in field_events[0]["data"]["fields"]

    def test_detects_move_in_with_underscore_normalization(self):
        session, q = self._make_session_with_broadcaster()
        from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
        state = WORKFLOW_DEF.states["greet_and_gather"]

        session._detect_field_progress(state, "I want to move in March", "Move in March, perfect!")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) == 1
        assert "move_in" in field_events[0]["data"]["fields"]

    def test_no_detection_when_no_fields_mentioned(self):
        session, q = self._make_session_with_broadcaster()
        from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
        state = WORKFLOW_DEF.states["greet_and_gather"]

        session._detect_field_progress(state, "hello", "Hi there! How can I help?")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) == 0

    def test_no_detection_for_state_without_state_fields(self):
        session, q = self._make_session_with_broadcaster()
        from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
        state = WORKFLOW_DEF.states["hello"]

        session._detect_field_progress(state, "I need bedrooms", "Sure!")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) == 0

    def test_detects_multiple_fields_at_once(self):
        session, q = self._make_session_with_broadcaster()
        from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
        state = WORKFLOW_DEF.states["greet_and_gather"]

        session._detect_field_progress(
            state,
            "I need 2 bedrooms downtown, budget about 2000",
            "Two bedrooms in the downtown area for two thousand, got it!",
        )

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        field_events = [e for e in events if e["type"] == "field_progress"]
        assert len(field_events) == 1
        fields = field_events[0]["data"]["fields"]
        assert "bedrooms" in fields
        assert "budget" in fields
        assert "area" in fields


# ── Event type coverage tests ───────────────────────────────────────


class TestEventTypes:
    """Verify all expected event types can be emitted."""

    def _collect_events(self, session, q):
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events

    def test_stt_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-stt")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("stt", {"text": "hello"})

        events = self._collect_events(session, q)
        assert any(e["type"] == "stt" for e in events)

    def test_llm_call_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-llm-call")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("llm_call", {
            "system_prompt": "You are...",
            "user_text": "hello",
        })

        events = self._collect_events(session, q)
        assert any(e["type"] == "llm_call" for e in events)
        assert events[0]["data"]["user_text"] == "hello"

    def test_llm_response_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-llm-resp")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("llm_response", {
            "response": "Hi there! How can I help?",
            "has_json_signal": False,
        })

        events = self._collect_events(session, q)
        assert any(e["type"] == "llm_response" for e in events)
        assert events[0]["data"]["response"] == "Hi there! How can I help?"

    def test_transition_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-trans")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("transition", {
            "from": "hello",
            "to": "greet_and_gather",
            "intent": "greeted",
        })

        events = self._collect_events(session, q)
        assert any(e["type"] == "transition" for e in events)
        assert events[0]["data"]["from"] == "hello"
        assert events[0]["data"]["to"] == "greet_and_gather"

    def test_tool_exec_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-tool")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("tool_exec", {
            "tool_name": "apartment_search",
            "args": {"query": "2 bed downtown"},
            "result": "Found 3 listings...",
        })

        events = self._collect_events(session, q)
        assert any(e["type"] == "tool_exec" for e in events)

    def test_step_complete_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-step")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("step_complete", {
            "extracted_data": {"bedrooms": 2, "budget": 2000},
        })

        events = self._collect_events(session, q)
        assert any(e["type"] == "step_complete" for e in events)
        assert events[0]["data"]["extracted_data"]["bedrooms"] == 2

    def test_field_progress_event(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-field")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("field_progress", {
            "fields": {"bedrooms": True, "budget": True},
        })

        events = self._collect_events(session, q)
        assert any(e["type"] == "field_progress" for e in events)

    def test_pause_resume_events(self):
        session = SchedulingSession()
        b = DebugBroadcaster("evt-pause")
        q = b.subscribe()
        session.attach_broadcaster(b)

        session._emit_event("pause", {})
        session._emit_event("resume", {})

        events = self._collect_events(session, q)
        types = [e["type"] for e in events]
        assert "pause" in types
        assert "resume" in types


# ── System prompt rendering — no PROGRESS leak ──────────────────────


class TestNoProgressLeak:
    """Ensure the PROGRESS tracking instruction is NOT in system prompts."""

    def test_greet_gather_prompt_has_no_progress(self):
        session = SchedulingSession()
        from scheduling.workflows.apartment_viewing import STEPS
        state = STEPS["greet_and_gather"]
        prompt = session._render_system_prompt(state)

        assert "PROGRESS" not in prompt
        assert "progress tracker" not in prompt.lower()

    def test_hello_prompt_has_no_progress(self):
        session = SchedulingSession()
        from scheduling.workflows.apartment_viewing import STEPS
        state = STEPS["hello"]
        prompt = session._render_system_prompt(state)

        assert "PROGRESS" not in prompt

    def test_prompt_has_tts_instruction(self):
        session = SchedulingSession()
        from scheduling.workflows.apartment_viewing import STEPS
        state = STEPS["greet_and_gather"]
        prompt = session._render_system_prompt(state)

        assert "text-to-speech" in prompt.lower()
        assert "null" in prompt.lower()  # The "NEVER say null" instruction

    def test_prompt_has_null_warning(self):
        session = SchedulingSession()
        from scheduling.workflows.apartment_viewing import STEPS
        state = STEPS["greet_and_gather"]
        prompt = session._render_system_prompt(state)

        assert 'NEVER say "null"' in prompt
