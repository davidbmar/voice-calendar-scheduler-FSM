"""Re-export from cal-provider package.

The canonical implementations now live in the ``cal-provider`` library.
This module preserves backward compatibility for all FSM imports.
"""

from cal_provider import CalendarEvent, CalendarProvider, TimeSlot

__all__ = ["CalendarProvider", "CalendarEvent", "TimeSlot"]
