"""Booking tool for the voice assistant.

The LLM calls ``create_booking`` after the caller has confirmed the viewing
details.  It creates a Google Calendar event and returns a confirmation
string.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Import BaseTool from the engine-repo package.
# ---------------------------------------------------------------------------
_ENGINE_REPO = os.path.join(os.path.dirname(__file__), "..", "..", "engine-repo")
if _ENGINE_REPO not in sys.path:
    sys.path.insert(0, _ENGINE_REPO)

from voice_assistant.tools.base import BaseTool  # noqa: E402

from scheduling.calendar_providers.base import (  # noqa: E402
    CalendarEvent,
    CalendarProvider,
)
from scheduling.config import settings  # noqa: E402

logger = logging.getLogger(__name__)


class CreateBookingTool(BaseTool):
    """Book an apartment viewing on the calendar.

    Parameters accepted from the LLM:

    * ``listing_address`` -- Address of the apartment.
    * ``date``            -- Date string ``YYYY-MM-DD``.
    * ``time``            -- Time string ``HH:MM`` (24-hour).
    * ``name``            -- Caller's name.
    * ``email``           -- Caller's email address.
    """

    def __init__(
        self,
        provider: CalendarProvider,
        calendar_id: str = "primary",
        duration_minutes: int = 30,
    ) -> None:
        self._provider = provider
        self._calendar_id = calendar_id
        self._duration_minutes = duration_minutes

    # ---- BaseTool interface ------------------------------------------------

    @property
    def name(self) -> str:
        return "create_booking"

    @property
    def description(self) -> str:
        return (
            "Book an apartment viewing by creating a calendar event. "
            "Requires the listing address, date, time, caller name, and email."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "listing_address": {
                    "type": "string",
                    "description": "Street address of the apartment listing.",
                },
                "date": {
                    "type": "string",
                    "description": "Viewing date in YYYY-MM-DD format.",
                },
                "time": {
                    "type": "string",
                    "description": "Viewing time in HH:MM (24-hour) format.",
                },
                "name": {
                    "type": "string",
                    "description": "Full name of the caller.",
                },
                "email": {
                    "type": "string",
                    "description": "Email address of the caller.",
                },
            },
            "required": ["listing_address", "date", "time", "name", "email"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Create the calendar event and return a confirmation string."""
        listing_address: str = kwargs["listing_address"]
        date_str: str = kwargs["date"]
        time_str: str = kwargs["time"]
        caller_name: str = kwargs["name"]
        caller_email: str = kwargs["email"]

        # Parse start time in local timezone
        local_tz = ZoneInfo(settings.calendar_timezone)
        try:
            start_dt = datetime.strptime(
                f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=local_tz)
        except ValueError:
            return (
                f"Invalid date/time: {date_str} {time_str}. "
                "Please use YYYY-MM-DD and HH:MM formats."
            )

        end_dt = start_dt + timedelta(minutes=self._duration_minutes)

        event = CalendarEvent(
            summary=f"Apartment Viewing - {listing_address}",
            start=start_dt,
            end=end_dt,
            description=(
                f"Apartment viewing for {caller_name} ({caller_email}).\n"
                f"Property: {listing_address}"
            ),
            attendees=[caller_email],
            location=listing_address,
        )

        try:
            result = await self._provider.create_event(
                calendar_id=self._calendar_id,
                event=event,
            )
        except Exception:
            logger.exception("Failed to create booking event")
            return (
                "Sorry, I was unable to create the booking. "
                "Please try again or call back later."
            )

        event_id = result.get("event_id", "unknown")
        html_link = result.get("html_link", "")

        confirmation = (
            f"Booking confirmed!\n"
            f"  Event ID: {event_id}\n"
            f"  What: Apartment viewing at {listing_address}\n"
            f"  When: {start_dt.strftime('%A, %B %d at %I:%M %p')}\n"
            f"  Who: {caller_name} ({caller_email})"
        )
        if html_link:
            confirmation += f"\n  Calendar link: {html_link}"

        return confirmation
