/**
 * renameState.ts — Rename a state ID across an entire workflow definition.
 * Pure function: mutates the def in-place, no DOM dependency.
 */

import type { WorkflowDef } from './workflow.js';

export function renameState(def: WorkflowDef, oldId: string, newId: string): void {
  if (def.states[newId]) return; // target ID already taken
  const state = def.states[oldId];
  if (!state) return;

  // Move state entry
  state.id = newId;
  def.states[newId] = state;
  delete def.states[oldId];

  // Update initial_state
  if (def.initial_state === oldId) {
    def.initial_state = newId;
  }

  // Re-wire all transitions that reference oldId
  for (const s of Object.values(def.states)) {
    for (const [intent, target] of Object.entries(s.transitions)) {
      if (target === oldId) {
        s.transitions[intent] = newId;
      } else {
        const colonIdx = target.indexOf(':');
        if (colonIdx !== -1 && target.slice(0, colonIdx) === oldId) {
          s.transitions[intent] = newId + target.slice(colonIdx);
        }
      }
    }
    if (s.max_turns_target === oldId) {
      s.max_turns_target = newId;
    } else if (s.max_turns_target) {
      const colonIdx = s.max_turns_target.indexOf(':');
      if (colonIdx !== -1 && s.max_turns_target.slice(0, colonIdx) === oldId) {
        s.max_turns_target = newId + s.max_turns_target.slice(colonIdx);
      }
    }
  }
}
