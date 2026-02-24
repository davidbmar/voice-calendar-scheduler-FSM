"""8-step apartment viewing scheduling workflow FSM.

This module now loads from the JSONL branching workflow definition
and exports backward-compatible STEPS, STEP_ORDER, FIRST_STEP for
existing code that depends on the linear chain interface.

The canonical definition lives in data/workflows/apartment_viewing.jsonl
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scheduling.workflows.loader import load_workflow_jsonl
from scheduling.workflows.schema import SchedulingWorkflowDef


# ── Legacy dataclass (kept for backward compat with tests) ───────

@dataclass
class SchedulingStep:
    """One state in the scheduling FSM (legacy linear format)."""

    id: str
    name: str
    step_type: str  # "llm" (conversational) or "tool" (auto-execute)
    system_prompt: str = ""
    tool_names: list[str] = field(default_factory=list)
    next_step: str = ""
    narration: str = ""  # Spoken to caller before this step runs


# ── Load the branching workflow from JSONL ───────────────────────

_JSONL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "workflows" / "apartment_viewing.jsonl"

WORKFLOW_DEF: SchedulingWorkflowDef = load_workflow_jsonl(_JSONL_PATH)

# ── Workflow metadata ─────────────────────────────────────────────

WORKFLOW_ID = WORKFLOW_DEF.id
WORKFLOW_NAME = "Apartment Viewing Scheduling"
WORKFLOW_KEYWORDS = list(WORKFLOW_DEF.trigger_keywords)


# ── Build backward-compatible STEPS dict ─────────────────────────
#
# The linear step order is derived from the branching workflow by
# following the "happy path" — the first non-cancel transition
# from each state.  This preserves the original 8-step chain for
# tests that assert STEP_ORDER connectivity.

def _build_linear_order(wf: SchedulingWorkflowDef) -> list[str]:
    """Walk the happy path through the branching workflow."""
    order = []
    visited = set()
    current = wf.initial_state

    while current and current not in visited:
        state = wf.states.get(current)
        if not state:
            break
        visited.add(current)
        order.append(current)

        # Follow the first non-exit, non-error transition
        next_id = ""
        # Preferred intents in priority order for the happy path
        happy_intents = [
            state.auto_intent,  # tool steps default to "success"
            "gathered", "selected", "time_selected", "confirmed",
            "done", "success",
        ]
        for intent in happy_intents:
            target = state.transitions.get(intent, "")
            if target and not target.startswith("exit"):
                next_id = target.split(":")[0]
                break
        if not next_id:
            # Try wildcard
            target = state.transitions.get("*", "")
            if target and not target.startswith("exit"):
                next_id = target.split(":")[0]

        current = next_id

    return order


def _build_steps_dict(wf: SchedulingWorkflowDef, order: list[str]) -> dict[str, SchedulingStep]:
    """Build legacy STEPS dict from workflow definition."""
    steps: dict[str, SchedulingStep] = {}

    for i, state_id in enumerate(order):
        state = wf.states[state_id]
        next_step = order[i + 1] if i + 1 < len(order) else ""

        steps[state_id] = SchedulingStep(
            id=state_id,
            name=state.on_enter or state_id.replace("_", " ").title(),
            step_type=state.step_type,
            system_prompt=state.system_prompt,
            tool_names=list(state.tool_names),
            next_step=next_step,
            narration=state.narration,
        )

    return steps


STEP_ORDER: list[str] = _build_linear_order(WORKFLOW_DEF)
STEPS: dict[str, SchedulingStep] = _build_steps_dict(WORKFLOW_DEF, STEP_ORDER)
FIRST_STEP: str = STEP_ORDER[0] if STEP_ORDER else ""
