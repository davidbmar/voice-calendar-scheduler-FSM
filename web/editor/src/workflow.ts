/**
 * Workflow interfaces and types for the scheduling FSM editor.
 * Extended from speaker-workflow-system with scheduling-specific fields.
 */

// ── Interfaces ──────────────────────────────────────────────────────

export interface WorkflowStateDef {
  id: string;
  on_enter: string;
  step_type: string;                       // "llm" or "tool"
  system_prompt: string;
  tool_names: string[];
  narration: string;
  transitions: Record<string, string>;     // intent → stateId | "exit:msg"
  handler?: string;
  max_turns?: number;
  max_turns_target?: string;
  state_fields: Record<string, string>;    // JSON key → CallerState field
  tool_args_map: Record<string, string>;   // tool param → state data path
  auto_intent: string;                     // default intent for tool steps
}

export interface WorkflowDef {
  id: string;
  trigger_intent: string;
  initial_state: string;
  exit_phrases: string[];
  exit_message: string;
  trigger_keywords: string[];
  ui: {
    indicatorLabel?: string;
    indicatorHint?: string;
    bubbleClass?: string;
  };
  states: Record<string, WorkflowStateDef>;
}
