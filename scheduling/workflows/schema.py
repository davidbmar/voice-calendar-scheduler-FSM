"""Pydantic models for branching scheduling workflows.

Extends the speaker-workflow-system format with scheduling-specific
fields: step_type (llm/tool), system_prompt, tool_names, narration,
state_fields, and tool_args_map.
"""

from __future__ import annotations

from pydantic import BaseModel


class SchedulingStateDef(BaseModel):
    """One state in a branching scheduling workflow."""

    id: str
    on_enter: str = ""                     # Narration / spoken message
    step_type: str = "llm"                 # "llm" or "tool"
    system_prompt: str = ""                # LLM system prompt
    tool_names: list[str] = []             # Tools for tool steps
    narration: str = ""                    # Spoken before execution
    transitions: dict[str, str] = {}       # intent -> target state (branching)
    handler: str | None = None             # "accumulate", "bullets", etc.
    max_turns: int | None = None
    max_turns_target: str | None = None
    state_fields: dict[str, str] = {}      # JSON signal key -> CallerState field
    tool_args_map: dict[str, str] = {}     # tool param -> state data path
    auto_intent: str = "success"           # default intent for tool steps


class SchedulingWorkflowDef(BaseModel):
    """A complete branching workflow definition."""

    id: str
    trigger_intent: str = ""
    initial_state: str = ""
    exit_phrases: list[str] = []
    exit_message: str = ""
    trigger_keywords: list[str] = []
    ui: dict = {}
    states: dict[str, SchedulingStateDef] = {}
