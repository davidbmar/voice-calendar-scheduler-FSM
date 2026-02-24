/**
 * api.ts — API client for workflow CRUD and session monitoring.
 */

import type { WorkflowDef } from './workflow.js';

const BASE = '';  // Same origin — Vite proxy handles /api in dev

export async function fetchWorkflow(workflowId: string): Promise<WorkflowDef> {
  const resp = await fetch(`${BASE}/api/workflow/${workflowId}`);
  if (!resp.ok) throw new Error(`Failed to fetch workflow: ${resp.status}`);
  return resp.json();
}

export async function saveWorkflow(workflowId: string, def: WorkflowDef): Promise<WorkflowDef> {
  const resp = await fetch(`${BASE}/api/workflow/${workflowId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(def),
  });
  if (!resp.ok) throw new Error(`Failed to save workflow: ${resp.status}`);
  return resp.json();
}

export async function patchWorkflowState(
  workflowId: string,
  stateId: string,
  updates: Record<string, unknown>,
): Promise<unknown> {
  const resp = await fetch(`${BASE}/api/workflow/${workflowId}/states/${stateId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!resp.ok) throw new Error(`Failed to patch state: ${resp.status}`);
  return resp.json();
}

export interface SessionSummary {
  session_id: string;
  current_step_id: string;
  is_done: boolean;
  started_at: number;
  caller_state: Record<string, unknown>;
}

export async function fetchSessions(): Promise<{ sessions: SessionSummary[]; count: number }> {
  const resp = await fetch(`${BASE}/api/fsm/sessions`);
  if (!resp.ok) throw new Error(`Failed to fetch sessions: ${resp.status}`);
  return resp.json();
}

export async function pauseSession(sessionId: string): Promise<void> {
  const resp = await fetch(`${BASE}/api/fsm/sessions/${sessionId}/pause`, { method: 'POST' });
  if (!resp.ok) throw new Error(`Failed to pause session: ${resp.status}`);
}

export async function resumeSession(sessionId: string): Promise<void> {
  const resp = await fetch(`${BASE}/api/fsm/sessions/${sessionId}/resume`, { method: 'POST' });
  if (!resp.ok) throw new Error(`Failed to resume session: ${resp.status}`);
}

export interface DebugContext {
  session_id: string;
  current_step_id: string;
  is_done: boolean;
  is_paused: boolean;
  started_at: number;
  duration: number;
  caller_state: Record<string, unknown>;
  step_data: Record<string, unknown>;
  recent_messages: Array<{ role: string; content: string }>;
  event_log: Array<{
    type: string;
    timestamp: number;
    session_id: string;
    state_id: string;
    data: Record<string, unknown>;
  }>;
}

export async function fetchDebugContext(sessionId: string): Promise<DebugContext> {
  const resp = await fetch(`${BASE}/api/fsm/sessions/${sessionId}/debug-context`);
  if (!resp.ok) throw new Error(`Failed to fetch debug context: ${resp.status}`);
  return resp.json();
}
