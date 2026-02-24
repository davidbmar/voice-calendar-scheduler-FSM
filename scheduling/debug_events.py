"""Per-session debug event broadcaster for real-time call tracing.

Each SchedulingSession can have a DebugBroadcaster attached.  When events
are emitted (transitions, LLM calls, tool executions, etc.), they are
pushed to every connected subscriber's asyncio.Queue for delivery over
WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TypedDict

log = logging.getLogger("scheduling.debug_events")


class DebugEvent(TypedDict):
    type: str          # transition | llm_call | llm_response | tool_exec | stt | step_complete | error
    timestamp: float
    session_id: str
    state_id: str
    data: dict


class DebugBroadcaster:
    """Per-session event broadcaster using asyncio.Queue per subscriber."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._subscribers: list[asyncio.Queue[DebugEvent]] = []
        self._event_log: list[DebugEvent] = []

    def subscribe(self) -> asyncio.Queue[DebugEvent]:
        """Create a new subscriber queue and return it."""
        q: asyncio.Queue[DebugEvent] = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        log.info("Debug subscriber added for session %s (total: %d)",
                 self._session_id, len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[DebugEvent]) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
        log.info("Debug subscriber removed for session %s (total: %d)",
                 self._session_id, len(self._subscribers))

    def emit(self, event_type: str, state_id: str, data: dict) -> None:
        """Broadcast an event to all subscribers and append to event log."""
        event: DebugEvent = {
            "type": event_type,
            "timestamp": time.time(),
            "session_id": self._session_id,
            "state_id": state_id,
            "data": data,
        }
        self._event_log.append(event)

        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest event to make room
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def event_log(self) -> list[DebugEvent]:
        """Full event history for debug context export."""
        return list(self._event_log)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# ── Global broadcaster registry ──────────────────────────────────────

_broadcasters: dict[str, DebugBroadcaster] = {}


def get_broadcaster(session_id: str) -> DebugBroadcaster:
    """Get or create a broadcaster for a session."""
    if session_id not in _broadcasters:
        _broadcasters[session_id] = DebugBroadcaster(session_id)
        log.info("DebugBroadcaster created for session %s", session_id)
    return _broadcasters[session_id]


def remove_broadcaster(session_id: str) -> None:
    """Remove a broadcaster when the session ends."""
    if session_id in _broadcasters:
        del _broadcasters[session_id]
        log.info("DebugBroadcaster removed for session %s", session_id)
