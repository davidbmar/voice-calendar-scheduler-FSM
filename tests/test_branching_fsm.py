"""Tests for branching FSM session routing.

Tests the intent-based transition routing in SchedulingSession
without making actual LLM calls — validating that the FSM engine
correctly resolves transitions, parses targets, and manages state.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.session import SchedulingSession
from scheduling.workflows.schema import SchedulingStateDef, SchedulingWorkflowDef
from scheduling.workflows.apartment_viewing import WORKFLOW_DEF


# ── Helpers ─────────────────────────────────────────────────────

def _make_minimal_workflow(**overrides) -> SchedulingWorkflowDef:
    """Build a minimal workflow for testing transition routing."""
    defaults = {
        "id": "test_wf",
        "initial_state": "start",
        "exit_phrases": ["cancel"],
        "exit_message": "Goodbye!",
        "states": {
            "start": SchedulingStateDef(
                id="start",
                step_type="llm",
                system_prompt="You are a test assistant.",
                transitions={"next": "middle", "cancel": "exit:Bye!"},
            ),
            "middle": SchedulingStateDef(
                id="middle",
                step_type="llm",
                system_prompt="Middle state.",
                transitions={"done": "exit:All done!", "*": "start"},
            ),
        },
    }
    defaults.update(overrides)
    return SchedulingWorkflowDef(**defaults)


# ── Target parsing tests ───────────────────────────────────────

class TestResolveTarget:
    def test_simple_state(self):
        session = SchedulingSession()
        state_id, msg = session._resolve_target("middle")
        assert state_id == "middle"
        assert msg == ""

    def test_state_with_message(self):
        session = SchedulingSession()
        state_id, msg = session._resolve_target("middle:Override message")
        assert state_id == "middle"
        assert msg == "Override message"

    def test_exit_no_message(self):
        session = SchedulingSession()
        state_id, msg = session._resolve_target("exit")
        assert state_id == ""
        # Should use workflow exit_message
        assert msg == WORKFLOW_DEF.exit_message

    def test_exit_with_message(self):
        session = SchedulingSession()
        state_id, msg = session._resolve_target("exit:Custom goodbye")
        assert state_id == ""
        assert msg == "Custom goodbye"

    def test_empty_target(self):
        session = SchedulingSession()
        state_id, msg = session._resolve_target("")
        assert state_id == ""
        assert msg == ""


# ── Transition routing tests ───────────────────────────────────

class TestResolveTransition:
    def test_matching_intent(self):
        wf = _make_minimal_workflow()
        session = SchedulingSession(workflow=wf)
        state = wf.states["start"]

        result = session._resolve_transition(state, "next")
        assert result is not None
        assert session.current_step == "middle"
        assert not session.is_done

    def test_exit_transition(self):
        wf = _make_minimal_workflow()
        session = SchedulingSession(workflow=wf)
        state = wf.states["start"]

        result = session._resolve_transition(state, "cancel")
        assert result is None
        assert session.is_done

    def test_wildcard_fallback(self):
        wf = _make_minimal_workflow()
        session = SchedulingSession(workflow=wf)
        # Advance to middle state
        session._current_step_id = "middle"
        state = wf.states["middle"]

        # "unknown_intent" should fall back to "*" -> "start"
        result = session._resolve_transition(state, "unknown_intent")
        assert result is not None
        assert session.current_step == "start"

    def test_no_matching_intent_stays(self):
        wf = _make_minimal_workflow()
        session = SchedulingSession(workflow=wf)
        state = wf.states["start"]

        # "nonexistent" doesn't match any transition and there's no "*"
        result = session._resolve_transition(state, "nonexistent")
        # Should stay on current state
        assert result is not None
        assert result.id == "start"
        assert session.current_step == "start"

    def test_done_after_exit(self):
        wf = _make_minimal_workflow()
        session = SchedulingSession(workflow=wf)
        session._current_step_id = "middle"
        state = wf.states["middle"]

        session._resolve_transition(state, "done")
        assert session.is_done


# ── Step completion with state_fields ──────────────────────────

class TestStepCompletionWithStateFields:
    @pytest.mark.asyncio
    async def test_data_driven_state_fields_mapping(self):
        """state_fields should map JSON keys to CallerState fields."""
        session = SchedulingSession()
        state = WORKFLOW_DEF.states["greet_and_gather"]

        data = {"bedrooms": 3, "budget": 2500, "area": "east side"}
        await session._process_step_completion(state, data)

        assert session.caller_state.bedrooms == 3
        assert session.caller_state.max_budget == 2500
        assert session.caller_state.preferred_area == "east side"

    @pytest.mark.asyncio
    async def test_state_fields_step_data_target(self):
        """state_fields targeting step_data.X should store in _step_data."""
        session = SchedulingSession()
        state = WORKFLOW_DEF.states["greet_and_gather"]

        data = {
            "bedrooms": 2,
            "budget": 1500,
            "extras": "pet friendly, parking",
        }
        await session._process_step_completion(state, data)

        # "extras" maps to "step_data.preferences" via state_fields
        # But the full data also goes to step_data["preferences"] via legacy code
        assert session._step_data.get("preferences") is not None

    @pytest.mark.asyncio
    async def test_present_options_state_fields(self):
        session = SchedulingSession()
        state = WORKFLOW_DEF.states["present_options"]

        data = {
            "selected_listing_id": "apt-007",
            "selected_address": "789 Oak Lane",
        }
        await session._process_step_completion(state, data)

        assert session.caller_state.selected_listing_id == "apt-007"
        assert session.caller_state.selected_listing_address == "789 Oak Lane"

    @pytest.mark.asyncio
    async def test_collect_details_state_fields(self):
        session = SchedulingSession()
        state = WORKFLOW_DEF.states["collect_details"]

        data = {"name": "Alice Smith", "email": "alice@example.com", "confirmed": True}
        await session._process_step_completion(state, data)

        assert session.caller_state.caller_name == "Alice Smith"
        assert session.caller_state.caller_email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_propose_times_stores_selection(self):
        session = SchedulingSession()
        state = WORKFLOW_DEF.states["propose_times"]

        data = {"selected_date": "2026-04-10", "selected_time": "10:00"}
        await session._process_step_completion(state, data)

        assert session._step_data["selected_date"] == "2026-04-10"
        assert session._step_data["selected_time"] == "10:00"
        assert "2026-04-10" in session.caller_state.selected_time_slot


# ── Tool args from map ────────────────────────────────────────

class TestToolArgsMap:
    def test_create_booking_args_from_map(self):
        """create_booking should build args from tool_args_map."""
        session = SchedulingSession()
        session._state.selected_listing_address = "123 Main St"
        session._state.caller_name = "Bob"
        session._state.caller_email = "bob@test.com"
        session._step_data["selected_date"] = "2026-05-01"
        session._step_data["selected_time"] = "15:00"

        state = WORKFLOW_DEF.states["create_booking"]
        args = session._build_tool_args(state, "create_booking")

        assert args["listing_address"] == "123 Main St"
        assert args["date"] == "2026-05-01"
        assert args["time"] == "15:00"
        assert args["name"] == "Bob"
        assert args["email"] == "bob@test.com"

    def test_search_listings_uses_legacy_builder(self):
        """search_listings has empty tool_args_map — should fall through to legacy."""
        session = SchedulingSession()
        session._step_data["preferences"] = {
            "bedrooms": 1,
            "area": "south austin",
            "budget": 1200,
        }

        state = WORKFLOW_DEF.states["search_listings"]
        args = session._build_tool_args(state, "apartment_search")

        assert "1 bedroom" in args["query"]
        assert "south austin" in args["query"]
        assert "1200" in args["query"]

    def test_legacy_string_arg_building(self):
        """Backward compat: _build_tool_args accepts string step_id."""
        session = SchedulingSession()
        session._step_data["preferences"] = {
            "bedrooms": 2,
            "area": "downtown",
            "budget": 2000,
        }

        args = session._build_tool_args("search_listings", "apartment_search")
        assert "2 bedroom" in args["query"]
        assert "downtown" in args["query"]

    def test_resolve_data_path_state_field(self):
        session = SchedulingSession()
        session._state.caller_name = "Test User"

        result = session._resolve_data_path("state.caller_name")
        assert result == "Test User"

    def test_resolve_data_path_step_data(self):
        session = SchedulingSession()
        session._step_data["my_key"] = "my_value"

        result = session._resolve_data_path("step_data.my_key")
        assert result == "my_value"

    def test_resolve_data_path_literal(self):
        session = SchedulingSession()
        result = session._resolve_data_path("5")
        assert result == "5"

    def test_check_availability_literal_days_ahead(self):
        """check_availability uses literal '5' for days_ahead."""
        state = WORKFLOW_DEF.states["check_availability"]
        assert state.tool_args_map.get("days_ahead") == "5"


# ── System prompt rendering ───────────────────────────────────

class TestSystemPromptRendering:
    def test_search_results_placeholder(self):
        session = SchedulingSession()
        session._step_data["search_listings"] = "Apt A: 123 Main, Apt B: 456 Elm"

        state = WORKFLOW_DEF.states["present_options"]
        prompt = session._render_system_prompt(state)

        assert "123 Main" in prompt
        assert "456 Elm" in prompt
        assert "{{search_results}}" not in prompt

    def test_available_slots_placeholder(self):
        session = SchedulingSession()
        session._step_data["check_availability"] = "Mon 10am, Tue 2pm"

        state = WORKFLOW_DEF.states["propose_times"]
        prompt = session._render_system_prompt(state)

        assert "Mon 10am" in prompt
        assert "{{available_slots}}" not in prompt

    def test_booking_confirmation_placeholder(self):
        session = SchedulingSession()
        session._step_data["create_booking"] = "Event ID: evt_999"
        session._state.selected_listing_address = "789 Oak"
        session._state.caller_email = "user@test.com"

        state = WORKFLOW_DEF.states["confirm_done"]
        prompt = session._render_system_prompt(state)

        assert "evt_999" in prompt
        assert "789 Oak" in prompt
        assert "user@test.com" in prompt


# ── Session to_dict serialization ─────────────────────────────

class TestSessionSerialization:
    def test_to_dict_summary(self):
        session = SchedulingSession()
        d = session.to_dict()

        assert "session_id" in d
        assert "current_step_id" in d
        assert "is_done" in d
        assert d["current_step_id"] == "hello"
        assert d["is_done"] is False

    def test_to_dict_detail(self):
        session = SchedulingSession()
        session._step_data["test"] = "value"
        d = session.to_dict(detail=True)

        assert "step_data" in d
        assert "message_count" in d
        assert "recent_messages" in d


# ── JSONL loader/saver round-trip ─────────────────────────────

class TestLoaderRoundTrip:
    def test_load_and_verify(self):
        """The loaded workflow should match expected structure."""
        assert WORKFLOW_DEF.id == "apartment_viewing"
        assert WORKFLOW_DEF.initial_state == "hello"
        assert len(WORKFLOW_DEF.states) == 10

    def test_save_and_reload(self, tmp_path):
        """Saving and reloading a workflow should preserve all data."""
        from scheduling.workflows.loader import save_workflow_jsonl, load_workflow_jsonl

        out_path = tmp_path / "test_workflow.jsonl"
        save_workflow_jsonl(WORKFLOW_DEF, out_path)

        reloaded = load_workflow_jsonl(out_path)

        assert reloaded.id == WORKFLOW_DEF.id
        assert reloaded.initial_state == WORKFLOW_DEF.initial_state
        assert len(reloaded.states) == len(WORKFLOW_DEF.states)
        assert set(reloaded.states.keys()) == set(WORKFLOW_DEF.states.keys())

        # Verify transitions preserved
        for state_id in WORKFLOW_DEF.states:
            orig = WORKFLOW_DEF.states[state_id]
            loaded = reloaded.states[state_id]
            assert orig.transitions == loaded.transitions
            assert orig.step_type == loaded.step_type
            assert orig.tool_names == loaded.tool_names

    def test_save_creates_parent_dirs(self, tmp_path):
        from scheduling.workflows.loader import save_workflow_jsonl

        deep_path = tmp_path / "a" / "b" / "c" / "wf.jsonl"
        save_workflow_jsonl(WORKFLOW_DEF, deep_path)
        assert deep_path.exists()
