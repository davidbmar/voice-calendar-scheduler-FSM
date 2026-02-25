"""Tests for CalendarProvider ABC and GoogleCalendarProvider."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.calendar_providers.base import (
    CalendarEvent,
    CalendarProvider,
    TimeSlot,
)


# ── TimeSlot / CalendarEvent dataclass tests ────────────────────────


class TestDataclasses:
    def test_timeslot_creation(self):
        now = datetime.now(tz=timezone.utc)
        slot = TimeSlot(start=now, end=now + timedelta(hours=1))
        assert (slot.end - slot.start).total_seconds() == 3600

    def test_calendar_event_defaults(self):
        now = datetime.now(tz=timezone.utc)
        event = CalendarEvent(
            summary="Test",
            start=now,
            end=now + timedelta(minutes=30),
        )
        assert event.description == ""
        assert event.attendees == []
        assert event.location == ""

    def test_calendar_event_with_attendees(self):
        now = datetime.now(tz=timezone.utc)
        event = CalendarEvent(
            summary="Viewing",
            start=now,
            end=now + timedelta(minutes=30),
            attendees=["a@test.com", "b@test.com"],
            location="123 Main St",
        )
        assert len(event.attendees) == 2
        assert event.location == "123 Main St"


# ── ABC contract tests ─────────────────────────────────────────────


class TestCalendarProviderABC:
    def test_cannot_instantiate(self):
        """CalendarProvider is abstract — can't be instantiated directly."""
        with pytest.raises(TypeError):
            CalendarProvider()

    def test_concrete_implementation(self):
        """A concrete subclass must implement all abstract methods."""
        class MockProvider(CalendarProvider):
            async def list_calendars(self):
                return []
            async def get_available_slots(self, calendar_id, start, end, duration_minutes=60):
                return []
            async def get_events(self, calendar_id, start, end):
                return []
            async def create_event(self, calendar_id, event):
                return {}
            async def cancel_event(self, calendar_id, event_id):
                return True

        provider = MockProvider()
        assert isinstance(provider, CalendarProvider)


# ── GoogleCalendarProvider tests (mocked API) ──────────────────────


class TestGoogleCalendarProvider:
    @pytest.fixture
    def mock_provider(self):
        """Create a GoogleCalendarProvider with mocked Google APIs."""
        with patch(
            "cal_provider.providers.google.Credentials"
        ) as mock_creds, patch(
            "cal_provider.providers.google.build"
        ) as mock_build:
            mock_creds.from_service_account_file.return_value = MagicMock()

            from scheduling.calendar_providers.google import GoogleCalendarProvider

            provider = GoogleCalendarProvider(
                service_account_path="/fake/path.json"
            )
            provider._service = mock_build.return_value
            return provider

    @pytest.mark.asyncio
    async def test_get_available_slots_empty_calendar(self, mock_provider):
        """Fully free calendar should return one big slot."""
        start = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 15, 18, 0, tzinfo=timezone.utc)

        # Mock freebusy response with no busy blocks
        mock_provider._service.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {
                "primary": {"busy": []}
            }
        }

        slots = await mock_provider.get_available_slots("primary", start, end, 30)

        assert len(slots) == 1
        assert slots[0].start == start
        assert slots[0].end == end

    @pytest.mark.asyncio
    async def test_get_available_slots_with_busy(self, mock_provider):
        """Busy blocks should create gaps."""
        start = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 15, 18, 0, tzinfo=timezone.utc)

        mock_provider._service.freebusy.return_value.query.return_value.execute.return_value = {
            "calendars": {
                "primary": {
                    "busy": [
                        {
                            "start": "2026-03-15T10:00:00+00:00",
                            "end": "2026-03-15T11:00:00+00:00",
                        },
                        {
                            "start": "2026-03-15T14:00:00+00:00",
                            "end": "2026-03-15T15:00:00+00:00",
                        },
                    ]
                }
            }
        }

        slots = await mock_provider.get_available_slots("primary", start, end, 30)

        # Should have: 9-10, 11-14, 15-18
        assert len(slots) == 3
        assert slots[0].start.hour == 9
        assert slots[0].end.hour == 10
        assert slots[1].start.hour == 11
        assert slots[1].end.hour == 14
        assert slots[2].start.hour == 15
        assert slots[2].end.hour == 18

    @pytest.mark.asyncio
    async def test_create_event(self, mock_provider):
        """create_event should call events().insert() and return event data."""
        now = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc)
        event = CalendarEvent(
            summary="Apartment Viewing",
            start=now,
            end=now + timedelta(minutes=30),
            attendees=["test@example.com"],
            location="123 Main St",
        )

        mock_provider._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "evt_123",
            "htmlLink": "https://calendar.google.com/event/evt_123",
            "status": "confirmed",
        }

        result = await mock_provider.create_event("primary", event)

        assert result["event_id"] == "evt_123"
        assert result["html_link"] == "https://calendar.google.com/event/evt_123"

    @pytest.mark.asyncio
    async def test_cancel_event(self, mock_provider):
        """cancel_event should call events().delete()."""
        mock_provider._service.events.return_value.delete.return_value.execute.return_value = None

        result = await mock_provider.cancel_event("primary", "evt_123")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_event_failure(self, mock_provider):
        """cancel_event should return False on error."""
        mock_provider._service.events.return_value.delete.return_value.execute.side_effect = Exception(
            "Not found"
        )

        result = await mock_provider.cancel_event("primary", "evt_404")
        assert result is False
