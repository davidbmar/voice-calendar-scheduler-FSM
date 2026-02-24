"""Abstract base class for calendar providers.

Defines the interface for checking availability and creating events.
Any calendar backend (Google, Outlook, etc.) implements this ABC.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TimeSlot:
    """A window of availability on a calendar."""

    start: datetime
    end: datetime


@dataclass
class CalendarEvent:
    """Represents a calendar event to be created."""

    summary: str
    start: datetime
    end: datetime
    description: str = ""
    attendees: list[str] = field(default_factory=list)  # email addresses
    location: str = ""


class CalendarProvider(ABC):
    """Abstract calendar backend.

    Subclasses must implement availability checking, event creation,
    and event cancellation.
    """

    @abstractmethod
    async def get_available_slots(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        duration_minutes: int = 60,
    ) -> list[TimeSlot]:
        """Return available time slots within the given range.

        Args:
            calendar_id: The calendar to query.
            start: Beginning of the search window.
            end: End of the search window.
            duration_minutes: Minimum slot length in minutes.

        Returns:
            List of TimeSlot objects that are free and at least
            ``duration_minutes`` long.
        """

    @abstractmethod
    async def create_event(
        self, calendar_id: str, event: CalendarEvent
    ) -> dict:
        """Create a calendar event.

        Args:
            calendar_id: The calendar to create the event on.
            event: Event details.

        Returns:
            Dict containing at least ``"event_id"`` and ``"html_link"``.
        """

    @abstractmethod
    async def cancel_event(
        self, calendar_id: str, event_id: str
    ) -> bool:
        """Cancel / delete a calendar event.

        Args:
            calendar_id: The calendar that owns the event.
            event_id: Provider-specific event identifier.

        Returns:
            True if the event was successfully cancelled.
        """
