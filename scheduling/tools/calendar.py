"""Check-availability tool for the voice assistant.

The LLM calls ``check_availability`` with a date (and optional lookahead
window) to receive a formatted list of open time slots from the connected
calendar provider.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Import BaseTool from the engine-repo package that sits alongside this tree.
# ---------------------------------------------------------------------------
_ENGINE_REPO = os.path.join(os.path.dirname(__file__), "..", "..", "engine-repo")
if _ENGINE_REPO not in sys.path:
    sys.path.insert(0, _ENGINE_REPO)

from voice_assistant.tools.base import BaseTool  # noqa: E402

from scheduling.calendar_providers.base import CalendarProvider  # noqa: E402

logger = logging.getLogger(__name__)


class CheckAvailabilityTool(BaseTool):
    """Return available viewing slots from the calendar.

    Parameters accepted from the LLM:

    * ``date``  -- ISO date string (``YYYY-MM-DD``).  Defaults to today.
    * ``days_ahead`` -- How many days to search forward (default **3**).
    """

    def __init__(
        self,
        provider: CalendarProvider,
        calendar_id: str = "primary",
    ) -> None:
        self._provider = provider
        self._calendar_id = calendar_id

    # ---- BaseTool interface ------------------------------------------------

    @property
    def name(self) -> str:
        return "check_availability"

    @property
    def description(self) -> str:
        return (
            "Check the calendar for available viewing time slots. "
            "Returns a list of open slots over the requested date range."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": (
                        "Start date in YYYY-MM-DD format. Defaults to today."
                    ),
                },
                "days_ahead": {
                    "type": "integer",
                    "description": (
                        "Number of days ahead to search for availability. "
                        "Defaults to 3."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Query the calendar provider and format results for the LLM."""
        date_str: str = kwargs.get("date", "")
        days_ahead: int = int(kwargs.get("days_ahead", 3))

        if date_str:
            try:
                start_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return f"Invalid date format: {date_str!r}. Please use YYYY-MM-DD."
        else:
            now = datetime.now(tz=timezone.utc)
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Search window: start_date 09:00 to (start_date + days_ahead) 18:00
        range_start = start_date.replace(hour=9, minute=0, second=0)
        range_end = (start_date + timedelta(days=days_ahead)).replace(
            hour=18, minute=0, second=0
        )

        try:
            slots = await self._provider.get_available_slots(
                calendar_id=self._calendar_id,
                start=range_start,
                end=range_end,
                duration_minutes=30,
            )
        except Exception:
            logger.exception("Failed to query calendar availability")
            return "Sorry, I could not check the calendar right now. Please try again."

        if not slots:
            return (
                f"No available slots found between "
                f"{range_start.strftime('%Y-%m-%d')} and "
                f"{range_end.strftime('%Y-%m-%d')}."
            )

        lines = ["Available time slots:"]
        for slot in slots:
            day = slot.start.strftime("%A, %B %d")
            start_t = slot.start.strftime("%I:%M %p")
            end_t = slot.end.strftime("%I:%M %p")
            lines.append(f"  - {day}: {start_t} to {end_t}")

        return "\n".join(lines)
