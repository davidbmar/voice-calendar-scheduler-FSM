"""Pydantic model tracking the caller's state through the conversation."""

from typing import Optional

from pydantic import BaseModel


class CallerState(BaseModel):
    """Mutable session state for a single inbound call.

    Fields are populated progressively as the voice assistant gathers
    information from the caller during the scheduling conversation.
    """

    call_sid: str = ""
    phone_number: str = ""

    # Preferences gathered during conversation
    bedrooms: Optional[int] = None
    max_budget: Optional[int] = None
    preferred_area: Optional[str] = None
    move_in_date: Optional[str] = None

    # Selected listing
    selected_listing_id: Optional[str] = None
    selected_listing_address: Optional[str] = None

    # Booking details
    selected_time_slot: Optional[str] = None
    caller_name: Optional[str] = None
    caller_email: Optional[str] = None

    # Booking result
    booking_event_id: Optional[str] = None
    booking_confirmed: bool = False
