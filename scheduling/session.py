"""Per-call scheduling session — drives the branching FSM through voice conversation.

Each inbound call (Twilio or WebRTC) gets a SchedulingSession that:
  1. Holds the CallerState (preferences, selected listing, booking)
  2. Tracks the current FSM state in a branching workflow
  3. For LLM states: wraps the engine Orchestrator with state-specific
     system prompts and tool schemas
  4. For tool states: auto-executes the tool and routes via intent
  5. Feeds caller utterances (from STT) through the current state
  6. Returns response text (for TTS) after each utterance
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Optional

from engine.orchestrator import Orchestrator, OrchestratorConfig

from scheduling.calendar_providers.base import CalendarProvider
from scheduling.config import settings
from scheduling.models.caller import CallerState
from scheduling.tools.apartment_search import ApartmentSearchTool
from scheduling.tools.booking import CreateBookingTool
from scheduling.tools.calendar import CheckAvailabilityTool
from scheduling.debug_events import DebugBroadcaster
from scheduling.workflows.schema import SchedulingStateDef, SchedulingWorkflowDef

# Backward compat: also import legacy types so existing code can do
# `from scheduling.session import SchedulingStep`
from scheduling.workflows.apartment_viewing import (  # noqa: F401
    FIRST_STEP,
    STEPS,
    STEP_ORDER,
    SchedulingStep,
    WORKFLOW_DEF as _DEFAULT_WORKFLOW,
)

log = logging.getLogger("scheduling.session")


def redact_pii(value: str) -> str:
    """Mask PII for logging — show first 3 and last 2 chars only."""
    if not value or len(value) <= 5:
        return "***"
    return value[:3] + "***" + value[-2:]


# ── Session registry ─────────────────────────────────────────────

_active_sessions: dict[str, "SchedulingSession"] = {}


def register_session(session: "SchedulingSession") -> str:
    """Register a session and return its unique ID."""
    session_id = secrets.token_urlsafe(18)
    session._session_id = session_id
    session._started_at = time.time()
    _active_sessions[session_id] = session
    log.info("Session registered: %s", session_id)
    return session_id


def unregister_session(session_id: str) -> None:
    """Remove a session from the registry."""
    _active_sessions.pop(session_id, None)
    log.info("Session unregistered: %s", session_id)


def get_active_sessions() -> dict[str, "SchedulingSession"]:
    """Return all active sessions."""
    return _active_sessions


def get_session(session_id: str) -> "SchedulingSession | None":
    """Look up a session by ID."""
    return _active_sessions.get(session_id)


class SchedulingSession:
    """One voice call's scheduling conversation.

    Typical lifecycle::

        session = SchedulingSession(workflow=my_workflow, calendar_provider=provider)
        session.start(caller_info={"phone_number": "+1...", "call_sid": "CA..."})

        # First turn — the assistant greets the caller
        greeting = await session.get_greeting()
        # → TTS speaks greeting

        # Subsequent turns — caller speaks, STT produces text
        while not session.is_done:
            response = await session.handle_utterance(caller_text)
            # → TTS speaks response
    """

    def __init__(
        self,
        workflow: SchedulingWorkflowDef | None = None,
        calendar_provider: Optional[CalendarProvider] = None,
        calendar_id: str = "",
    ) -> None:
        self._workflow = workflow or _DEFAULT_WORKFLOW
        self._calendar_provider = calendar_provider
        self._calendar_id = calendar_id or settings.google_calendar_id

        # Registry metadata (set by register_session)
        self._session_id: str = ""
        self._started_at: float = 0.0

        # FSM state — use branching workflow's initial state
        self._current_step_id: str = self._workflow.initial_state
        self._state = CallerState()
        self._step_data: dict[str, Any] = {}  # Intermediate results

        # Conversation history (kept across steps for context)
        self._messages: list[dict[str, str]] = []

        # Tools (instantiated once, reused)
        self._tools: dict[str, Any] = {}
        self._init_tools()

        self._done = False

        # Debug support
        self._debug_broadcaster: DebugBroadcaster | None = None
        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially

    # ── Helpers ────────────────────────────────────────────────

    def _get_state(self, state_id: str) -> SchedulingStateDef | None:
        """Look up a state in the workflow definition."""
        return self._workflow.states.get(state_id)

    def _current_state(self) -> SchedulingStateDef | None:
        """Get the current state definition."""
        return self._get_state(self._current_step_id)

    # ── Public API ────────────────────────────────────────────

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def current_step(self) -> str:
        return self._current_step_id

    @property
    def caller_state(self) -> CallerState:
        return self._state

    def attach_broadcaster(self, broadcaster: DebugBroadcaster) -> None:
        """Attach a debug broadcaster for real-time event streaming."""
        self._debug_broadcaster = broadcaster

    def _emit_event(self, event_type: str, data: dict) -> None:
        """Emit a debug event if a broadcaster is attached."""
        if self._debug_broadcaster:
            self._debug_broadcaster.emit(event_type, self._current_step_id, data)

    def pause(self) -> None:
        """Pause FSM processing. Audio still flows but handle_utterance blocks."""
        self._paused.clear()
        self._emit_event("pause", {})
        log.info("Session %s paused", self._session_id)

    def resume(self) -> None:
        """Resume FSM processing after a pause."""
        self._paused.set()
        self._emit_event("resume", {})
        log.info("Session %s resumed", self._session_id)

    @property
    def is_paused(self) -> bool:
        return not self._paused.is_set()

    def to_dict(self, detail: bool = False) -> dict[str, Any]:
        """Serialize session state for the API.

        With detail=False: summary suitable for listing.
        With detail=True: adds step_data, message count, recent messages.
        """
        d: dict[str, Any] = {
            "session_id": self._session_id,
            "current_step_id": self._current_step_id,
            "is_done": self._done,
            "is_paused": self.is_paused,
            "started_at": self._started_at,
            "caller_state": self._state.model_dump(),
            "step_data_keys": list(self._step_data.keys()),
        }
        if detail:
            # Truncate large step data values
            truncated = {}
            for k, v in self._step_data.items():
                s = str(v)
                truncated[k] = s[:500] + "..." if len(s) > 500 else s
            d["step_data"] = truncated
            d["message_count"] = len(self._messages)
            d["recent_messages"] = self._messages[-6:]
            if self._debug_broadcaster:
                d["event_log"] = self._debug_broadcaster.event_log
        return d

    def start(self, caller_info: dict[str, Any] | None = None) -> None:
        """Initialize session with caller metadata."""
        if caller_info:
            self._state.call_sid = caller_info.get("call_sid", "")
            self._state.phone_number = caller_info.get("phone_number", "")
        log.info(
            "Session started: step=%s phone=%s",
            self._current_step_id,
            redact_pii(self._state.phone_number),
        )

    async def get_greeting(self) -> str:
        """Generate the initial greeting (first LLM call)."""
        state = self._current_state()
        if not state:
            return "Hello! How can I help you?"

        system = self._render_system_prompt(state)

        greeting_prompt = (
            "A caller just connected. Greet them warmly. "
            "Keep it brief — just introduce yourself and welcome them."
        )

        response = await self._call_llm(system, greeting_prompt)
        return self._extract_text_response(response)

    async def handle_utterance(self, text: str) -> str:
        """Process one caller utterance and return the response text.

        This is the main loop driver. It:
          1. Sends the utterance to the LLM with the current state's prompt
          2. Checks if the LLM's response signals state completion
          3. If so, routes via intent through transitions (branching!)
          4. Returns the final text for TTS
        """
        # Block while paused (debug control)
        await self._paused.wait()

        if self._done:
            return "Thank you for calling. Goodbye!"

        state = self._current_state()
        if not state:
            return "I'm sorry, something went wrong. Goodbye!"

        self._emit_event("stt", {"text": text})

        if state.step_type == "llm":
            return await self._handle_llm_step(state, text)
        else:
            # Tool steps auto-execute — shouldn't receive utterances
            # but handle gracefully by routing to next LLM state
            next_state = self._resolve_transition(state, state.auto_intent)
            if next_state and next_state.step_type == "llm":
                return await self._handle_llm_step(next_state, text)
            return await self._handle_llm_step(state, text)

    # ── Internal: Transition routing ─────────────────────────

    def _resolve_target(self, target: str) -> tuple[str, str]:
        """Parse a transition target string.

        Returns (state_id, exit_message).
        - "stateId" → ("stateId", "")
        - "stateId:override msg" → ("stateId", "override msg")
        - "exit" → ("", "")
        - "exit:goodbye msg" → ("", "goodbye msg")
        """
        if not target:
            return "", ""

        if target.startswith("exit"):
            if ":" in target:
                _, msg = target.split(":", 1)
                return "", msg
            return "", self._workflow.exit_message
        elif ":" in target:
            state_id, msg = target.split(":", 1)
            return state_id, msg
        else:
            return target, ""

    def _resolve_transition(
        self, state: SchedulingStateDef, intent: str,
    ) -> SchedulingStateDef | None:
        """Look up the target state for an intent, apply the transition.

        Returns the new state, or None if the session should exit.
        """
        target = state.transitions.get(intent) or state.transitions.get("*")
        if not target:
            return state  # No matching transition — stay in current state

        state_id, exit_msg = self._resolve_target(target)

        if not state_id:
            # Exit transition
            self._done = True
            log.info("FSM exit from %s via intent '%s': %s", state.id, intent, exit_msg)
            return None

        self._current_step_id = state_id
        log.info("FSM advance: %s → %s (intent: %s)", state.id, state_id, intent)
        self._emit_event("transition", {
            "from": state.id, "to": state_id, "intent": intent,
        })
        return self._get_state(state_id)

    # ── Internal: LLM step handling ──────────────────────────

    async def _handle_llm_step(self, state: SchedulingStateDef, text: str) -> str:
        """Handle a conversational LLM step."""
        system = self._render_system_prompt(state)
        response = await self._call_llm(system, text)

        # Incremental field detection — light up pills as fields are discussed
        self._detect_field_progress(state, text, response)

        # Check for JSON completion signal
        extracted = self._extract_json_signal(response)

        if extracted is not None:
            # Step completed — process the structured data
            await self._process_step_completion(state, extracted)

            text_response = self._extract_text_response(response)

            # Route via intent from the JSON signal
            intent = extracted.get("intent", "success")
            next_state = self._resolve_transition(state, intent)

            if self._done:
                # Exit transition — return text + exit message
                _, exit_msg = self._resolve_target(
                    state.transitions.get(intent, "") or state.transitions.get("*", "")
                )
                return text_response or exit_msg or "Goodbye!"

            if next_state:
                # Auto-execute any tool steps in sequence
                tool_response = await self._run_tool_steps()
                # After transition (and any tool steps), generate the
                # opening for the next LLM state so the caller knows
                # what to say. This covers both LLM→LLM transitions
                # (hello → greet_and_gather) and tool→LLM transitions
                # (search_listings → present_options).
                current = self._current_state()
                if current and current.step_type == "llm":
                    follow_up = await self._get_step_opening(current)
                    return f"{text_response} {follow_up}".strip()
                return text_response

            return text_response
        else:
            # Step not complete — continue conversation
            return self._extract_text_response(response)

    async def _run_tool_steps(self) -> str | None:
        """Auto-execute consecutive tool steps, advancing via intent routing."""
        results = []

        while True:
            state = self._current_state()
            if not state or state.step_type != "tool":
                break

            log.info("Auto-executing tool step: %s", state.id)

            try:
                result = await self._execute_tool_step(state)
                results.append(result)
                self._step_data[state.id] = result
                intent = state.auto_intent  # "success" by default
            except Exception as e:
                log.error("Tool step %s failed: %s", state.id, e)
                self._step_data[state.id] = f"Error: {e}"
                intent = "error"

            next_state = self._resolve_transition(state, intent)
            if self._done or not next_state:
                break

        return "\n".join(results) if results else None

    async def _execute_tool_step(self, state: SchedulingStateDef) -> str:
        """Execute a tool step and return its result."""
        results = []

        for tool_name in state.tool_names:
            tool = self._tools.get(tool_name)
            if not tool:
                log.warning("Tool not found: %s", tool_name)
                results.append(f"Tool {tool_name} not available")
                continue

            args = self._build_tool_args(state, tool_name)
            log.info("Calling tool %s with args: %s", tool_name, args)

            result = await tool.execute(**args)
            self._emit_event("tool_exec", {
                "tool_name": tool_name,
                "args": args,
                "result": str(result)[:200],
            })
            results.append(result)

        return "\n".join(results)

    async def _get_step_opening(self, state: SchedulingStateDef) -> str:
        """Generate the LLM's opening for a new conversational step.

        Uses the state's on_enter field from the JSONL workflow definition
        as the prompt. This is fully data-driven — editing on_enter in the
        editor changes what the LLM says when entering that state. Falls back
        to a generic prompt if on_enter is empty.
        """
        system = self._render_system_prompt(state)

        # Data-driven: use on_enter from the JSONL workflow definition
        if state.on_enter:
            prompt = (
                f"You are now entering this conversation step. "
                f"Say this to the caller (rephrase naturally): {state.on_enter}"
            )
        else:
            prompt = "Continue the conversation."

        return await self._call_llm(system, prompt)

    # ── Internal: LLM calls ──────────────────────────────────

    async def _call_llm(self, system_prompt: str, user_text: str) -> str:
        """Call the Orchestrator with the given system prompt."""
        config = OrchestratorConfig(
            provider=settings.llm_provider,
            system_prompt=system_prompt,
            max_iterations=1,
        )
        orch = Orchestrator(config=config)

        # Inject conversation history for context
        orch.messages = list(self._messages)

        self._emit_event("llm_call", {
            "system_prompt": system_prompt[:100],
            "user_text": user_text,
        })

        reply = await orch.chat(user_text)

        self._emit_event("llm_response", {
            "response": reply,
            "has_json_signal": bool(self._extract_json_signal(reply)),
        })

        # Save to our history
        self._messages.append({"role": "user", "content": user_text})
        self._messages.append({"role": "assistant", "content": reply})

        # Trim history to avoid context overflow
        if len(self._messages) > 30:
            self._messages = self._messages[-20:]

        return reply

    # ── Internal: Field progress detection ─────────────────────

    def _detect_field_progress(
        self, state: SchedulingStateDef, user_text: str, llm_response: str,
    ) -> None:
        """Detect which state_fields are being discussed and emit field_progress.

        Scans the user's utterance and the LLM's response for mentions of
        each field key. This is a passive heuristic — it doesn't require
        the LLM to emit any special tracking format.
        """
        state_fields = getattr(state, "state_fields", {}) or {}
        if not state_fields:
            return

        combined = (user_text + " " + llm_response).lower()
        detected = {}

        for key in state_fields:
            # Normalize key for matching (e.g. "move_in" → "move in", "move-in")
            patterns = [key.lower(), key.replace("_", " "), key.replace("_", "-")]
            for pat in patterns:
                if pat in combined:
                    detected[key] = True
                    break

        if detected:
            self._emit_event("field_progress", {"fields": detected})

    # ── Internal: JSON signal extraction ──────────────────────

    @staticmethod
    def _extract_json_signal(text: str) -> dict | None:
        """Extract a JSON completion signal from LLM output.

        The LLM outputs a fenced JSON block when a step is complete.
        Returns the parsed dict, or None if no signal found.
        """
        # Try fenced code blocks first
        pattern = r"```(?:json)?\s*\n?({.*?})\s*\n?```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try bare JSON on its own line
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        return None

    @staticmethod
    def _extract_text_response(text: str) -> str:
        """Remove JSON blocks from LLM output, keeping spoken text."""
        # Remove fenced code blocks
        cleaned = re.sub(r"```(?:json)?\s*\n?{.*?}\s*\n?```", "", text, flags=re.DOTALL)
        # Remove bare JSON lines
        lines = []
        for line in cleaned.split("\n"):
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    json.loads(stripped)
                    continue  # Skip JSON lines
                except json.JSONDecodeError:
                    pass
            lines.append(line)
        return "\n".join(lines).strip()

    # ── Internal: Step completion processing ──────────────────

    async def _process_step_completion(
        self, state: SchedulingStateDef | SchedulingStep, data: dict,
    ) -> None:
        """Update CallerState from the JSON signal using data-driven state_fields mapping."""
        state_id = state.id

        # Data-driven mapping: state_fields maps JSON keys to CallerState fields
        state_fields = getattr(state, "state_fields", {}) or {}

        if state_fields:
            for json_key, target in state_fields.items():
                value = data.get(json_key)
                if value is None:
                    continue

                if target.startswith("step_data."):
                    # Store in step_data dict
                    data_key = target.split(".", 1)[1]
                    self._step_data[data_key] = value
                else:
                    # Set on CallerState
                    if hasattr(self._state, target):
                        setattr(self._state, target, value)
        else:
            # Fallback: legacy hardcoded mapping for SchedulingStep objects
            if state_id == "greet_and_gather":
                self._state.bedrooms = data.get("bedrooms")
                self._state.max_budget = data.get("budget")
                self._state.preferred_area = data.get("area")
            elif state_id == "present_options":
                self._state.selected_listing_id = data.get("selected_listing_id")
                self._state.selected_listing_address = data.get("selected_address")
            elif state_id == "collect_details":
                self._state.caller_name = data.get("name")
                self._state.caller_email = data.get("email")

        # Also store the full JSON data for backward compat
        if state_id == "greet_and_gather":
            self._step_data["preferences"] = data

        # Handle time slot composition for propose_times
        if state_id == "propose_times":
            selected_date = data.get("selected_date", "")
            selected_time = data.get("selected_time", "")
            self._state.selected_time_slot = f"{selected_date} {selected_time}".strip()
            self._step_data["selected_date"] = selected_date
            self._step_data["selected_time"] = selected_time

        # Handle terminal state
        if state_id == "confirm_done" or data.get("done"):
            self._done = True

        self._emit_event("step_complete", {"extracted_data": data})
        log.info("Step %s completed", state_id)
        log.debug("Step %s caller state: %s", state_id, self._state)

    # ── Internal: Tool argument builders ──────────────────────

    def _build_tool_args(
        self,
        state: SchedulingStateDef | str,
        tool_name: str,
    ) -> dict:
        """Build tool arguments from the current session state.

        Uses the state's tool_args_map for data-driven arg building,
        with fallback to hardcoded builders for backward compat.

        Accepts either a SchedulingStateDef or a step_id string for
        backward compatibility.
        """
        if isinstance(state, str):
            # Legacy call: _build_tool_args("step_id", "tool_name")
            state_def = self._get_state(state)
            if state_def and state_def.tool_args_map:
                return self._build_tool_args_from_map(state_def.tool_args_map)
            return self._build_tool_args_legacy(state, tool_name)

        if state.tool_args_map:
            return self._build_tool_args_from_map(state.tool_args_map)

        # Fallback: hardcoded builders (backward compat)
        return self._build_tool_args_legacy(state.id, tool_name)

    def _build_tool_args_from_map(self, args_map: dict[str, str]) -> dict:
        """Build tool args from a declarative mapping."""
        args = {}
        for param, source in args_map.items():
            args[param] = self._resolve_data_path(source)
        return args

    def _resolve_data_path(self, path: str) -> Any:
        """Resolve a data path like 'state.caller_name' or 'step_data.preferences'."""
        if path.startswith("state."):
            field = path.split(".", 1)[1]
            return getattr(self._state, field, "")
        elif path.startswith("step_data."):
            key = path.split(".", 1)[1]
            return self._step_data.get(key, "")
        else:
            # Literal value
            return path

    def _build_tool_args_legacy(self, step_id: str, tool_name: str) -> dict:
        """Legacy hardcoded tool arg building (backward compat)."""
        if tool_name == "apartment_search":
            prefs = self._step_data.get("preferences", {})
            parts = []
            if prefs.get("bedrooms"):
                parts.append(f"{prefs['bedrooms']} bedroom")
            if prefs.get("area"):
                parts.append(f"near {prefs['area']}")
            if prefs.get("budget"):
                parts.append(f"under ${prefs['budget']}")
            if prefs.get("extras"):
                parts.append(str(prefs["extras"]))
            return {"query": " ".join(parts) or "apartment"}

        elif tool_name == "check_availability":
            return {
                "date": self._step_data.get("selected_date", ""),
                "days_ahead": 5,
            }

        elif tool_name == "create_booking":
            return {
                "listing_address": self._state.selected_listing_address or "",
                "date": self._step_data.get("selected_date", ""),
                "time": self._step_data.get("selected_time", ""),
                "name": self._state.caller_name or "",
                "email": self._state.caller_email or "",
            }

        return {}

    # ── Internal: System prompt rendering ─────────────────────

    def _render_system_prompt(self, state: SchedulingStateDef) -> str:
        """Render a state's system prompt with current state data.

        Replaces {{placeholder}} patterns with values from CallerState
        and step_data, and appends TTS-friendly formatting instructions.
        """
        prompt = state.system_prompt

        # TTS-friendly formatting: responses are read aloud, so numbers
        # should be written as spoken words in conversational text.
        tts_instruction = (
            "\n\nFORMATTING: Your responses will be read aloud by text-to-speech. "
            "Write all numbers as spoken words in your conversational text "
            "(e.g., say \"two thousand five hundred dollars a month\" not \"$2,500/mo\", "
            "\"three bedrooms\" not \"3 bedrooms\", \"fourteen hundred square feet\" not \"1,400 sq ft\"). "
            "This only applies to your spoken text — JSON output blocks must still use numeric values."
            "\n\nCRITICAL: NEVER say \"null\", \"none\", \"not set\", \"no value\", \"N/A\", or "
            "\"not available\" to the caller. If a piece of information hasn't been gathered yet, "
            "simply skip it or don't mention it. Only reference information you actually have."
        )
        prompt = prompt + tts_instruction

        # Built-in replacements for common placeholders
        replacements = {
            "{{search_results}}": self._step_data.get("search_listings", ""),
            "{{available_slots}}": self._step_data.get("check_availability", ""),
            "{{selected_address}}": self._state.selected_listing_address or "",
            "{{selected_time_display}}": self._state.selected_time_slot or "",
            "{{caller_email}}": self._state.caller_email or "",
            "{{booking_confirmation}}": self._step_data.get("create_booking", ""),
        }

        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, str(value))

        return prompt

    # ── Internal: Tool initialization ─────────────────────────

    def _init_tools(self) -> None:
        """Instantiate the tools used by this session."""
        self._tools["apartment_search"] = ApartmentSearchTool()

        if self._calendar_provider:
            self._tools["check_availability"] = CheckAvailabilityTool(
                provider=self._calendar_provider,
                calendar_id=self._calendar_id,
            )
            self._tools["create_booking"] = CreateBookingTool(
                provider=self._calendar_provider,
                calendar_id=self._calendar_id,
            )
