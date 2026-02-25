# Session

Session-ID: S-2026-02-24-0628-extract-cal-provider
Title: Extract calendar providers into standalone cal-provider library
Date: 2026-02-24
Author: Claude

## Goal

Extract the calendar provider layer from the FSM project into a standalone Python library (`cal-provider`) supporting multiple backends (Google Calendar + CalDAV) with an optional MCP server wrapper.

## Context

The `CalendarProvider` ABC and `GoogleCalendarProvider` have zero domain imports — they depend only on `datetime`, `asyncio`, and Google API libraries. This makes them ideal for extraction into a reusable package. Other AI agent projects can then use it as a library or connect via MCP.

## Plan

1. Create repository and package scaffold
2. Extract models and ABC
3. Extract Google provider
4. Build CalDAV provider
5. Build provider registry
6. Build MCP server
7. Write tests
8. Update FSM project with re-export shim

## Changes Made

### New: cal-provider library (`/Users/davidmar/src/cal-provider/`)
- `pyproject.toml` — Package metadata, optional deps [google], [caldav], [mcp], [all]
- `src/cal_provider/models.py` — TimeSlot, CalendarEvent, CalendarInfo dataclasses
- `src/cal_provider/provider.py` — CalendarProvider ABC (5 abstract + 1 optional method)
- `src/cal_provider/utils.py` — `compute_available_slots()` busy→available inversion
- `src/cal_provider/registry.py` — `get_provider()` / `register_provider()` with lazy imports
- `src/cal_provider/providers/google.py` — GoogleCalendarProvider (migrated from FSM)
- `src/cal_provider/providers/caldav_provider.py` — CalDAVProvider (new)
- `src/cal_provider/mcp/server.py` — FastMCP server with 6 tools
- `src/cal_provider/mcp/config.py` — Env-var-based provider factory
- `tests/` — 40 tests across 6 test files (all passing)

### Modified: FSM project
- `scheduling/calendar_providers/base.py` — Re-export shim → `cal_provider`
- `scheduling/calendar_providers/__init__.py` — Re-export shim → `cal_provider`
- `scheduling/calendar_providers/google.py` — Re-export shim → `cal_provider.providers.google`
- `tests/test_calendar_provider.py` — Updated mock patch targets to canonical `cal_provider.providers.google`, added `list_calendars`/`get_events` to concrete ABC test

## Decisions Made

- **Separate repo** at `/Users/davidmar/src/cal-provider/` (not nested in FSM)
- **`update_event` is concrete, not abstract** — raises NotImplementedError by default to avoid forcing all backends to implement it
- **Lazy import pattern** for registry — optional deps only imported when `get_provider()` called
- **CalDAV uses event-fetch approach** for availability (not freebusy queries, which are unreliable)
- **Shared `compute_available_slots()`** — both Google and CalDAV feed busy intervals into the same algorithm
- **FSM test patch targets updated** — `scheduling.calendar_providers.google.Credentials` → `cal_provider.providers.google.Credentials` (necessary since shim no longer re-exports internals)

## Open Questions

- Should cal-provider be published to PyPI, or remain a local/private package?
- Should the CalDAV provider support OAuth2 in addition to HTTP Basic auth?

## Links

Commits:
- (pending — ready to commit)

ADRs:
- (none needed — pattern follows existing extraction conventions)
