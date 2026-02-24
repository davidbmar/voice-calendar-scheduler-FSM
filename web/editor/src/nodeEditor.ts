/**
 * nodeEditor.ts — Click-to-edit panel for workflow nodes.
 * Extended with scheduling fields: system prompt, step type, tool names, narration, state fields.
 */

import type { AppState, DomRefs, AppCallbacks } from './appContext.js';
import { parseTransitions, parseExitPhrases } from './editorUtils.js';
import { renameState } from './renameState.js';

export interface EditorField {
  key: string;
  label: string;
  value: string;
  multiline?: boolean;
}

export function openEditor(
  nodeId: string,
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  if (!state.currentDef) return;

  document.querySelectorAll('.wf-node.editing').forEach(n => n.classList.remove('editing'));
  const nodeEl = document.querySelector(`[data-node="${nodeId}"]`);
  nodeEl?.classList.add('editing');
  state.editingNodeId = nodeId;

  dom.nodeEditor.textContent = '';
  dom.nodeEditor.classList.add('open');
  dom.layoutEl.classList.add('editor-open');

  if (nodeId === 'idle') {
    renderEditorFields('Trigger', [
      { key: 'triggerIntent', label: 'Trigger Intent', value: state.currentDef.trigger_intent },
    ], state, dom, cb);
  } else if (nodeId === 'exit' || nodeId.startsWith('exit-')) {
    renderEditorFields('Exit', [
      { key: 'exitMessage', label: 'Exit Message', value: state.currentDef.exit_message, multiline: true },
      { key: 'exitPhrases', label: 'Exit Phrases (comma-separated)', value: state.currentDef.exit_phrases.join(', '), multiline: true },
    ], state, dom, cb);
  } else {
    const s = state.currentDef.states[nodeId];
    if (!s) return;
    const fields: EditorField[] = [
      { key: 'stateId', label: 'State ID (rename)', value: s.id },
      { key: 'stepType', label: 'Step Type (llm / tool)', value: s.step_type },
      { key: 'onEnter', label: 'On Enter Message', value: s.on_enter, multiline: true },
      { key: 'narration', label: 'Narration (spoken before execution)', value: s.narration, multiline: true },
      { key: 'systemPrompt', label: 'System Prompt', value: s.system_prompt, multiline: true },
    ];
    if (s.step_type === 'tool') {
      fields.push({ key: 'toolNames', label: 'Tool Names (comma-separated)', value: s.tool_names.join(', ') });
    }
    if (s.handler) {
      fields.push({ key: 'handler', label: 'Handler', value: s.handler });
    }
    const transKeys = Object.keys(s.transitions);
    if (transKeys.length > 0) {
      const transStr = transKeys.map(k => `${k} \u2192 ${s.transitions[k]}`).join('\n');
      fields.push({ key: 'transitions', label: 'Transitions (intent \u2192 target, one per line)', value: transStr, multiline: true });
    }
    // State fields mapping
    const sfKeys = Object.keys(s.state_fields);
    if (sfKeys.length > 0) {
      const sfStr = sfKeys.map(k => `${k} \u2192 ${s.state_fields[k]}`).join('\n');
      fields.push({ key: 'stateFields', label: 'State Fields (json_key \u2192 caller_field)', value: sfStr, multiline: true });
    }
    renderEditorFields(`State: ${nodeId}`, fields, state, dom, cb);
  }
}

function renderEditorFields(
  title: string,
  fields: EditorField[],
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  const titleRow = document.createElement('div');
  titleRow.className = 'editor-title';
  const titleSpan = document.createElement('span');
  titleSpan.textContent = title;
  const closeBtn = document.createElement('button');
  closeBtn.className = 'editor-close';
  closeBtn.textContent = '\u00D7';
  closeBtn.addEventListener('click', () => cb.closeEditor());
  titleRow.appendChild(titleSpan);
  titleRow.appendChild(closeBtn);
  dom.nodeEditor.appendChild(titleRow);

  const fieldEls: Record<string, HTMLInputElement | HTMLTextAreaElement> = {};

  for (const f of fields) {
    const container = document.createElement('div');
    container.className = 'editor-field';
    const label = document.createElement('label');
    label.textContent = f.label;
    container.appendChild(label);

    if (f.multiline) {
      const ta = document.createElement('textarea');
      ta.value = f.value;
      ta.rows = Math.min(8, f.value.split('\n').length + 1);
      container.appendChild(ta);
      fieldEls[f.key] = ta;
    } else {
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.value = f.value;
      container.appendChild(inp);
      fieldEls[f.key] = inp;
    }
    dom.nodeEditor.appendChild(container);
  }

  const saveBtn = document.createElement('button');
  saveBtn.className = 'editor-save';
  saveBtn.textContent = 'SAVE & RELOAD';
  saveBtn.addEventListener('click', () => applyEdits(fieldEls, state, cb));
  dom.nodeEditor.appendChild(saveBtn);
}

function applyEdits(
  fieldEls: Record<string, HTMLInputElement | HTMLTextAreaElement>,
  state: AppState,
  cb: AppCallbacks,
): void {
  if (!state.currentDef || !state.editingNodeId) return;
  const def = state.currentDef;

  if (state.editingNodeId === 'idle') {
    def.trigger_intent = fieldEls['triggerIntent']?.value.trim() || def.trigger_intent;
  } else if (state.editingNodeId === 'exit' || state.editingNodeId.startsWith('exit-')) {
    if (fieldEls['exitMessage']) {
      def.exit_message = fieldEls['exitMessage'].value;
    }
    if (fieldEls['exitPhrases']) {
      def.exit_phrases = parseExitPhrases(fieldEls['exitPhrases'].value);
    }
  } else {
    const s = def.states[state.editingNodeId];
    if (!s) return;

    if (fieldEls['stateId']) {
      const newId = fieldEls['stateId'].value.trim().toLowerCase().replace(/\s+/g, '_');
      if (newId && newId !== state.editingNodeId) {
        renameState(def, state.editingNodeId, newId);
      }
    }

    const currentId = fieldEls['stateId']
      ? (fieldEls['stateId'].value.trim().toLowerCase().replace(/\s+/g, '_') || state.editingNodeId)
      : state.editingNodeId;
    const updatedState = def.states[currentId];
    if (!updatedState) return;

    if (fieldEls['stepType']) {
      updatedState.step_type = fieldEls['stepType'].value.trim() || 'llm';
    }
    if (fieldEls['onEnter']) {
      updatedState.on_enter = fieldEls['onEnter'].value;
    }
    if (fieldEls['narration']) {
      updatedState.narration = fieldEls['narration'].value;
    }
    if (fieldEls['systemPrompt']) {
      updatedState.system_prompt = fieldEls['systemPrompt'].value;
    }
    if (fieldEls['toolNames']) {
      updatedState.tool_names = fieldEls['toolNames'].value.split(',').map(s => s.trim()).filter(Boolean);
    }
    if (fieldEls['handler']) {
      updatedState.handler = fieldEls['handler'].value.trim() || undefined;
    }
    if (fieldEls['transitions']) {
      updatedState.transitions = parseTransitions(fieldEls['transitions'].value);
    }
    if (fieldEls['stateFields']) {
      updatedState.state_fields = parseTransitions(fieldEls['stateFields'].value);
    }
  }

  cb.refreshAll();
  cb.closeEditor();
  cb.appendLog('system', `Config updated: ${state.editingNodeId}`);
}
