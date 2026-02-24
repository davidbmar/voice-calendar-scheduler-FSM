"""Pydantic models for booking requests and responses."""

from datetime import datetime

from pydantic import BaseModel


class BookingRequest(BaseModel):
    """Data collected from the caller to book an apartment viewing."""

    listing_address: str
    listing_id: str = ""
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    caller_name: str
    caller_email: str
    duration_minutes: int = 30


class BookingResponse(BaseModel):
    """Result returned after a booking attempt."""

    event_id: str
    confirmed: bool
    summary: str
    start_time: datetime
    end_time: datetime
    calendar_link: str = ""
