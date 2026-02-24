"""Load JSONL workflow definitions into SchedulingWorkflowDef objects."""

from __future__ import annotations

import json
from pathlib import Path

from scheduling.workflows.schema import SchedulingStateDef, SchedulingWorkflowDef


def load_workflow_jsonl(path: str | Path) -> SchedulingWorkflowDef:
    """Load a single workflow from a JSONL file.

    The JSONL file contains exactly one JSON object (the workflow).
    States are nested inside the top-level ``states`` dict.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8").strip()

    # JSONL: one JSON object per line — take the first non-empty line
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        return _parse_workflow(data)

    raise ValueError(f"No workflow found in {path}")


def load_workflows_jsonl(path: str | Path) -> dict[str, SchedulingWorkflowDef]:
    """Load multiple workflows from a JSONL file (one per line).

    Returns a dict keyed by workflow ID.
    """
    path = Path(path)
    workflows: dict[str, SchedulingWorkflowDef] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        wf = _parse_workflow(data)
        workflows[wf.id] = wf
    return workflows


def _parse_workflow(data: dict) -> SchedulingWorkflowDef:
    """Parse a raw dict into a SchedulingWorkflowDef."""
    # Parse nested states
    raw_states = data.get("states", {})
    states: dict[str, SchedulingStateDef] = {}
    for state_id, state_data in raw_states.items():
        if isinstance(state_data, dict):
            # Ensure id is set
            state_data.setdefault("id", state_id)
            states[state_id] = SchedulingStateDef(**state_data)
        else:
            states[state_id] = state_data

    data["states"] = states
    return SchedulingWorkflowDef(**data)


def save_workflow_jsonl(workflow: SchedulingWorkflowDef, path: str | Path) -> None:
    """Persist a workflow back to a JSONL file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = workflow.model_dump()
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
