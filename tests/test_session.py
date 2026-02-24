"""Tests for SchedulingSession — the per-call FSM driver."""

import json

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.session import SchedulingSession
from scheduling.workflows.apartment_viewing import FIRST_STEP


class TestSessionInit:
    def test_initial_state(self):
        session = SchedulingSession()
        assert session.current_step == FIRST_STEP
        assert session.is_done is False

    def test_start_populates_caller_info(self):
        session = SchedulingSession()
        session.start({
            "call_sid": "CA123",
            "phone_number": "+15551234567",
        })
        assert session.caller_state.call_sid == "CA123"
        assert session.caller_state.phone_number == "+15551234567"

    def test_tools_registered(self):
        session = SchedulingSession()
        # apartment_search is always available
        assert "apartment_search" in session._tools
        # Calendar tools only available with a provider
        assert "check_availability" not in session._tools


class TestJsonSignalExtraction:
    def test_fenced_json(self):
        text = 'Great, let me search.\n\n```json\n{"bedrooms": 2, "budget": 2000}\n```'
        result = SchedulingSession._extract_json_signal(text)
        assert result == {"bedrooms": 2, "budget": 2000}

    def test_bare_json(self):
        text = 'Sure thing.\n{"bedrooms": 1, "budget": 1500}'
        result = SchedulingSession._extract_json_signal(text)
        assert result == {"bedrooms": 1, "budget": 1500}

    def test_no_json(self):
        text = "Could you tell me how many bedrooms you need?"
        result = SchedulingSession._extract_json_signal(text)
        assert result is None

    def test_invalid_json(self):
        text = '```json\n{bedrooms: 2}\n```'
        result = SchedulingSession._extract_json_signal(text)
        assert result is None

    def test_json_in_conversation(self):
        text = (
            "Perfect! So you're looking for a 2 bedroom apartment near "
            "downtown for under $2000. Let me search for you.\n\n"
            '```json\n{"bedrooms": 2, "budget": 2000, "area": "downtown"}\n```'
        )
        result = SchedulingSession._extract_json_signal(text)
        assert result["bedrooms"] == 2
        assert result["area"] == "downtown"


class TestTextExtraction:
    def test_removes_fenced_json(self):
        text = 'Hello!\n\n```json\n{"done": true}\n```\n\nGoodbye!'
        cleaned = SchedulingSession._extract_text_response(text)
        assert "Hello!" in cleaned
        assert "Goodbye!" in cleaned
        assert "done" not in cleaned

    def test_removes_bare_json(self):
        text = 'Let me search.\n{"bedrooms": 2}'
        cleaned = SchedulingSession._extract_text_response(text)
        assert "search" in cleaned
        assert "bedrooms" not in cleaned

    def test_preserves_non_json_braces(self):
        text = 'The price range is {low} to {high}.'
        cleaned = SchedulingSession._extract_text_response(text)
        # Non-valid JSON braces should be kept
        assert "{low}" in cleaned


class TestStepCompletion:
    @pytest.mark.asyncio
    async def test_greet_gather_updates_state(self):
        session = SchedulingSession()
        data = {"bedrooms": 2, "budget": 2000, "area": "downtown"}

        from scheduling.workflows.apartment_viewing import STEPS
        step = STEPS["greet_and_gather"]
        await session._process_step_completion(step, data)

        assert session.caller_state.bedrooms == 2
        assert session.caller_state.max_budget == 2000
        assert session.caller_state.preferred_area == "downtown"

    @pytest.mark.asyncio
    async def test_present_options_updates_listing(self):
        session = SchedulingSession()
        data = {
            "selected_listing_id": "apt-003",
            "selected_address": "456 South Congress",
        }

        from scheduling.workflows.apartment_viewing import STEPS
        step = STEPS["present_options"]
        await session._process_step_completion(step, data)

        assert session.caller_state.selected_listing_id == "apt-003"
        assert session.caller_state.selected_listing_address == "456 South Congress"

    @pytest.mark.asyncio
    async def test_collect_details_updates_contact(self):
        session = SchedulingSession()
        data = {"name": "Jane Doe", "email": "jane@example.com", "confirmed": True}

        from scheduling.workflows.apartment_viewing import STEPS
        step = STEPS["collect_details"]
        await session._process_step_completion(step, data)

        assert session.caller_state.caller_name == "Jane Doe"
        assert session.caller_state.caller_email == "jane@example.com"


class TestToolArgBuilding:
    def test_apartment_search_args(self):
        session = SchedulingSession()
        session._step_data["preferences"] = {
            "bedrooms": 2,
            "area": "downtown",
            "budget": 2000,
        }

        args = session._build_tool_args("search_listings", "apartment_search")
        assert "2 bedroom" in args["query"]
        assert "downtown" in args["query"]
        assert "2000" in args["query"]

    def test_booking_args(self):
        session = SchedulingSession()
        session._state.selected_listing_address = "123 Main St"
        session._state.caller_name = "John"
        session._state.caller_email = "john@test.com"
        session._step_data["selected_date"] = "2026-03-15"
        session._step_data["selected_time"] = "14:00"

        args = session._build_tool_args("create_booking", "create_booking")
        assert args["listing_address"] == "123 Main St"
        assert args["date"] == "2026-03-15"
        assert args["time"] == "14:00"
        assert args["name"] == "John"
        assert args["email"] == "john@test.com"


class TestSystemPromptRendering:
    def test_present_options_injects_results(self):
        session = SchedulingSession()
        session._step_data["search_listings"] = "Option 1: 123 Main St..."

        from scheduling.workflows.apartment_viewing import STEPS
        step = STEPS["present_options"]
        prompt = session._render_system_prompt(step)

        assert "Option 1: 123 Main St" in prompt

    def test_confirm_done_injects_details(self):
        session = SchedulingSession()
        session._state.selected_listing_address = "456 Elm St"
        session._state.caller_email = "test@test.com"
        session._step_data["create_booking"] = "Event ID: evt_123"

        from scheduling.workflows.apartment_viewing import STEPS
        step = STEPS["confirm_done"]
        prompt = session._render_system_prompt(step)

        assert "456 Elm St" in prompt
        assert "test@test.com" in prompt
        assert "evt_123" in prompt
