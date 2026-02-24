/**
 * appContext.ts — Shared state, DOM refs, and callback interfaces.
 * The callback bag pattern breaks circular dependencies between modules.
 */

import type { WorkflowDef } from './workflow.js';

// ── DOM element references ───────────────────────────────────────────

export interface DomRefs {
  workflowMapEl: HTMLElement;
  nodeEditor: HTMLElement;
  layoutEl: Element;
  codeViewEl: HTMLElement;
  eventLog: HTMLElement;
  sessionMonitor: HTMLElement;
  modeBadge: HTMLElement;
}

// ── Callback bag (wired by orchestrator) ─────────────────────────────

export interface ArrowContext {
  intent: string;
  fromStateId?: string;
  target?: string;
  isExitPhrase?: boolean;
}

export interface AppCallbacks {
  openEditor: (nodeId: string) => void;
  openArrowEditor: (ctx: ArrowContext) => void;
  openAddStateEditor: (def: WorkflowDef) => void;
  closeEditor: () => void;
  refreshAll: () => void;
  highlightCodeBlock: (nodeId: string) => void;
  highlightCodeArrow: (fromState: string | undefined, intent: string) => void;
  appendLog: (type: string, text: string) => void;
  openLiveDebug: (sessionId: string) => void;
}

// ── Shared mutable state ─────────────────────────────────────────────

export interface AddStatePreview {
  stateId: string;
  onEnter: string;
  stepType: string;
  systemPrompt: string;
  toolNames: string[];
  handler?: string;
  maxTurns?: number;
  maxTurnsTarget?: string;
  transitions: Record<string, string>;
  connectFrom: string;
  connectIntent: string;
  intentKeywords: string[];
}

export type PreviewSelection = 'from-node' | 'incoming-arrow' | 'new-state' | 'outgoing';

export interface AppState {
  currentDef: WorkflowDef | null;
  editingNodeId: string | null;
  addStatePreview: AddStatePreview | null;
  previewSelection: PreviewSelection;
}

export function createAppState(): AppState {
  return {
    currentDef: null,
    editingNodeId: null,
    addStatePreview: null,
    previewSelection: 'new-state',
  };
}
