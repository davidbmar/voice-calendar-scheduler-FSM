"""Tests for the apartment viewing workflow FSM definition."""

import re

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine-repo"))

from scheduling.workflows.apartment_viewing import (
    FIRST_STEP,
    STEPS,
    STEP_ORDER,
    WORKFLOW_ID,
    WORKFLOW_KEYWORDS,
    WORKFLOW_NAME,
    SchedulingStep,
)


class TestWorkflowDefinition:
    def test_has_8_steps(self):
        assert len(STEPS) == 8
        assert len(STEP_ORDER) == 8

    def test_first_step(self):
        assert FIRST_STEP == "greet_and_gather"

    def test_step_order_matches_dict(self):
        """Every step in STEP_ORDER should exist in STEPS dict."""
        for step_id in STEP_ORDER:
            assert step_id in STEPS

    def test_chain_is_connected(self):
        """Each step's next_step should point to the next in the sequence."""
        for i, step_id in enumerate(STEP_ORDER[:-1]):
            step = STEPS[step_id]
            expected_next = STEP_ORDER[i + 1]
            assert step.next_step == expected_next, (
                f"Step {step_id} points to {step.next_step}, "
                f"expected {expected_next}"
            )

    def test_last_step_is_terminal(self):
        """The final step should have next_step == '' (terminal)."""
        last = STEPS[STEP_ORDER[-1]]
        assert last.next_step == ""

    def test_step_types(self):
        """Verify the expected step types."""
        expected_types = {
            "greet_and_gather": "llm",
            "search_listings": "tool",
            "present_options": "llm",
            "check_availability": "tool",
            "propose_times": "llm",
            "collect_details": "llm",
            "create_booking": "tool",
            "confirm_done": "llm",
        }
        for step_id, expected_type in expected_types.items():
            assert STEPS[step_id].step_type == expected_type

    def test_llm_steps_have_prompts(self):
        """All LLM steps must have non-empty system prompts."""
        for step_id in STEP_ORDER:
            step = STEPS[step_id]
            if step.step_type == "llm":
                assert step.system_prompt, f"LLM step {step_id} has no prompt"
                assert len(step.system_prompt) > 50

    def test_tool_steps_have_tool_names(self):
        """All tool steps must specify which tools to call."""
        for step_id in STEP_ORDER:
            step = STEPS[step_id]
            if step.step_type == "tool":
                assert step.tool_names, f"Tool step {step_id} has no tool_names"


class TestWorkflowMetadata:
    def test_workflow_id(self):
        assert WORKFLOW_ID == "apartment_viewing"

    def test_workflow_name(self):
        assert WORKFLOW_NAME == "Apartment Viewing Scheduling"

    def test_keywords_non_empty(self):
        assert len(WORKFLOW_KEYWORDS) >= 5

    def test_keywords_match_scheduling_intent(self):
        """Keywords should match common scheduling phrases."""
        keywords_lower = [k.lower() for k in WORKFLOW_KEYWORDS]
        assert "apartment" in keywords_lower
        assert "schedule" in keywords_lower or "book" in keywords_lower

    def test_keyword_routing(self):
        """Test regex-based routing against sample phrases."""
        pattern = re.compile(
            "|".join(r"\b" + kw + r"\b" for kw in WORKFLOW_KEYWORDS),
            re.IGNORECASE,
        )

        # Should match
        assert pattern.search("I'm looking for an apartment")
        assert pattern.search("I want to schedule a viewing")
        assert pattern.search("Can I book a tour?")
        assert pattern.search("I need a 2 bedroom place")
        assert pattern.search("I'm moving to Austin")

        # Should NOT match
        assert not pattern.search("What's the weather today?")
        assert not pattern.search("Tell me a joke")


# ── Branching workflow tests ───────────────────────────────────

from scheduling.workflows.apartment_viewing import WORKFLOW_DEF
from scheduling.workflows.schema import SchedulingStateDef


class TestBranchingTransitions:
    """Tests that validate the branching FSM structure."""

    def test_all_transitions_point_to_valid_states(self):
        """Every transition target (except exit) must reference a state that exists."""
        for state_id, state in WORKFLOW_DEF.states.items():
            for intent, target in state.transitions.items():
                if target.startswith("exit"):
                    continue
                target_id = target.split(":")[0]
                assert target_id in WORKFLOW_DEF.states, (
                    f"State '{state_id}' transition '{intent}' -> '{target_id}' "
                    f"points to nonexistent state"
                )

    def test_terminal_states_transition_to_exit(self):
        """States at the end of conversation paths must transition to exit."""
        confirm_done = WORKFLOW_DEF.states["confirm_done"]
        # confirm_done should exit on "done" and "*"
        assert "done" in confirm_done.transitions
        for target in confirm_done.transitions.values():
            assert target.startswith("exit"), (
                f"confirm_done transition '{target}' should be an exit"
            )

    def test_initial_state_exists(self):
        """The workflow's initial_state must be a valid state."""
        assert WORKFLOW_DEF.initial_state in WORKFLOW_DEF.states

    def test_initial_state_is_greet(self):
        assert WORKFLOW_DEF.initial_state == "greet_and_gather"


class TestBranchingPaths:
    """Tests for specific branching paths in the apartment workflow."""

    def test_greet_has_cancel_path(self):
        """greet_and_gather must allow the caller to cancel."""
        state = WORKFLOW_DEF.states["greet_and_gather"]
        assert "cancel" in state.transitions
        assert state.transitions["cancel"].startswith("exit")

    def test_greet_has_gathered_path(self):
        """greet_and_gather transitions to search_listings on 'gathered'."""
        state = WORKFLOW_DEF.states["greet_and_gather"]
        assert "gathered" in state.transitions
        assert state.transitions["gathered"].split(":")[0] == "search_listings"

    def test_present_options_has_search_again(self):
        """present_options must allow searching again (loop back)."""
        state = WORKFLOW_DEF.states["present_options"]
        assert "search_again" in state.transitions
        target = state.transitions["search_again"].split(":")[0]
        assert target == "greet_and_gather", (
            f"search_again should loop back to greet_and_gather, got '{target}'"
        )

    def test_present_options_has_cancel(self):
        state = WORKFLOW_DEF.states["present_options"]
        assert "cancel" in state.transitions
        assert state.transitions["cancel"].startswith("exit")

    def test_propose_times_has_no_times_exit(self):
        """propose_times must allow caller to exit if no times work."""
        state = WORKFLOW_DEF.states["propose_times"]
        assert "no_times" in state.transitions
        assert state.transitions["no_times"].startswith("exit")

    def test_propose_times_has_cancel(self):
        state = WORKFLOW_DEF.states["propose_times"]
        assert "cancel" in state.transitions
        assert state.transitions["cancel"].startswith("exit")

    def test_collect_details_has_cancel(self):
        state = WORKFLOW_DEF.states["collect_details"]
        assert "cancel" in state.transitions
        assert state.transitions["cancel"].startswith("exit")

    def test_tool_states_have_error_transition(self):
        """All tool steps must have an 'error' transition for graceful failure."""
        tool_states = [
            s for s in WORKFLOW_DEF.states.values() if s.step_type == "tool"
        ]
        assert len(tool_states) >= 3, "Expected at least 3 tool states"
        for state in tool_states:
            assert "error" in state.transitions, (
                f"Tool state '{state.id}' is missing an 'error' transition"
            )

    def test_tool_states_have_success_transition(self):
        """All tool steps must have a 'success' transition."""
        tool_states = [
            s for s in WORKFLOW_DEF.states.values() if s.step_type == "tool"
        ]
        for state in tool_states:
            assert "success" in state.transitions, (
                f"Tool state '{state.id}' is missing a 'success' transition"
            )

    def test_search_error_has_retry(self):
        """search_error must allow retrying the search."""
        state = WORKFLOW_DEF.states["search_error"]
        assert "retry" in state.transitions
        target = state.transitions["retry"].split(":")[0]
        assert target == "greet_and_gather"


class TestBranchingReachability:
    """BFS reachability — no orphan states."""

    def test_no_orphan_states(self):
        """Every state should be reachable from the initial state."""
        reachable = set()
        queue = [WORKFLOW_DEF.initial_state]

        while queue:
            current = queue.pop(0)
            if current in reachable:
                continue
            reachable.add(current)

            state = WORKFLOW_DEF.states.get(current)
            if not state:
                continue

            for target in state.transitions.values():
                if target.startswith("exit"):
                    continue
                target_id = target.split(":")[0]
                if target_id not in reachable:
                    queue.append(target_id)

        all_states = set(WORKFLOW_DEF.states.keys())
        orphans = all_states - reachable
        assert not orphans, f"Orphan states not reachable from initial: {orphans}"

    def test_every_state_can_reach_exit(self):
        """From every state, there should be some path to an exit transition."""
        for start_id in WORKFLOW_DEF.states:
            visited = set()
            queue = [start_id]
            found_exit = False

            while queue and not found_exit:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)

                state = WORKFLOW_DEF.states.get(current)
                if not state:
                    continue

                for target in state.transitions.values():
                    if target.startswith("exit"):
                        found_exit = True
                        break
                    target_id = target.split(":")[0]
                    if target_id not in visited:
                        queue.append(target_id)

            assert found_exit, (
                f"State '{start_id}' has no path to an exit transition"
            )


class TestWorkflowStateFields:
    """Tests for state_fields and tool_args_map data-driven configuration."""

    def test_greet_has_state_fields(self):
        """greet_and_gather should map JSON keys to CallerState fields."""
        state = WORKFLOW_DEF.states["greet_and_gather"]
        assert "bedrooms" in state.state_fields
        assert "budget" in state.state_fields
        assert state.state_fields["bedrooms"] == "bedrooms"
        assert state.state_fields["budget"] == "max_budget"

    def test_present_options_has_state_fields(self):
        state = WORKFLOW_DEF.states["present_options"]
        assert "selected_listing_id" in state.state_fields
        assert "selected_address" in state.state_fields

    def test_collect_details_has_state_fields(self):
        state = WORKFLOW_DEF.states["collect_details"]
        assert "name" in state.state_fields
        assert "email" in state.state_fields
        assert state.state_fields["name"] == "caller_name"
        assert state.state_fields["email"] == "caller_email"

    def test_create_booking_has_tool_args_map(self):
        state = WORKFLOW_DEF.states["create_booking"]
        m = state.tool_args_map
        assert "listing_address" in m
        assert "date" in m
        assert "time" in m
        assert "name" in m
        assert "email" in m
        assert m["listing_address"] == "state.selected_listing_address"
        assert m["name"] == "state.caller_name"

    def test_tool_steps_have_auto_intent(self):
        """Tool steps should have auto_intent set (usually 'success')."""
        for state in WORKFLOW_DEF.states.values():
            if state.step_type == "tool":
                assert state.auto_intent, f"Tool step '{state.id}' missing auto_intent"


class TestWorkflowSchemaValidation:
    """Tests for schema integrity of the loaded JSONL workflow."""

    def test_all_states_have_ids(self):
        for state_id, state in WORKFLOW_DEF.states.items():
            assert state.id == state_id

    def test_llm_states_have_system_prompts(self):
        for state in WORKFLOW_DEF.states.values():
            if state.step_type == "llm":
                assert state.system_prompt, (
                    f"LLM state '{state.id}' has no system_prompt"
                )
                assert len(state.system_prompt) > 50

    def test_tool_states_have_tool_names(self):
        for state in WORKFLOW_DEF.states.values():
            if state.step_type == "tool":
                assert state.tool_names, (
                    f"Tool state '{state.id}' has no tool_names"
                )

    def test_state_count(self):
        """The apartment workflow should have 9 states (original 8 + search_error)."""
        assert len(WORKFLOW_DEF.states) == 9

    def test_exit_phrases(self):
        assert len(WORKFLOW_DEF.exit_phrases) >= 3
        assert "cancel" in WORKFLOW_DEF.exit_phrases
        assert "goodbye" in WORKFLOW_DEF.exit_phrases

    def test_exit_message(self):
        assert WORKFLOW_DEF.exit_message
        assert len(WORKFLOW_DEF.exit_message) > 10
