/**
 * addStateEditor.ts — Visual editor for adding new states to a workflow.
 * Extended with step type selector (LLM/TOOL) and scheduling fields.
 */

import type { WorkflowDef, WorkflowStateDef } from './workflow.js';
import type { AppState, DomRefs, AppCallbacks, AddStatePreview, PreviewSelection } from './appContext.js';
import { INTENT_SIGNALS } from './intentClassifier.js';
import { parseTransitions, parseKeywords } from './editorUtils.js';
import { truncate } from './domUtils.js';

export function openAddStateEditor(
  def: WorkflowDef,
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  document.querySelectorAll('.wf-node.editing').forEach(n => n.classList.remove('editing'));
  document.querySelectorAll('.wf-arrow.editing').forEach(n => n.classList.remove('editing'));
  state.editingNodeId = '__add_state__';

  const existingIds = Object.keys(def.states);
  const lastState = existingIds[existingIds.length - 1] || def.initial_state;

  state.addStatePreview = {
    stateId: '',
    onEnter: '',
    stepType: 'llm',
    systemPrompt: '',
    toolNames: [],
    transitions: {},
    connectFrom: lastState,
    connectIntent: '',
    intentKeywords: [],
  };
  state.previewSelection = 'new-state';

  renderAddStatePanel(def, state, dom, cb);
}

function renderAddStatePanel(
  def: WorkflowDef,
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  dom.nodeEditor.textContent = '';
  dom.nodeEditor.classList.add('open');
  dom.layoutEl.classList.add('editor-open');

  const titleRow = document.createElement('div');
  titleRow.className = 'editor-title';
  const titleSpan = document.createElement('span');
  titleSpan.textContent = 'Add New State';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'editor-close';
  closeBtn.textContent = '\u00D7';
  closeBtn.addEventListener('click', () => cb.closeEditor());
  titleRow.appendChild(titleSpan);
  titleRow.appendChild(closeBtn);
  dom.nodeEditor.appendChild(titleRow);

  const previewContainer = document.createElement('div');
  previewContainer.className = 'add-state-preview';
  renderPreviewGraph(previewContainer, def, state, dom, cb);
  dom.nodeEditor.appendChild(previewContainer);

  const divider = document.createElement('div');
  divider.className = 'preview-divider';
  dom.nodeEditor.appendChild(divider);

  renderPreviewFields(def, state, dom, cb);

  const saveBtn = document.createElement('button');
  saveBtn.className = 'editor-save';
  saveBtn.textContent = 'CREATE STATE';
  saveBtn.addEventListener('click', () => applyAddState(def, state, cb));
  dom.nodeEditor.appendChild(saveBtn);
}

function renderPreviewGraph(
  container: HTMLElement,
  def: WorkflowDef,
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  if (!state.addStatePreview) return;
  const p = state.addStatePreview;

  const selectAndRerender = (sel: PreviewSelection) => {
    state.previewSelection = sel;
    renderAddStatePanel(def, state, dom, cb);
  };

  const fromLabel = p.connectFrom ? p.connectFrom.toUpperCase() : '???';
  const fromHint = p.connectFrom && def.states[p.connectFrom]
    ? truncate(def.states[p.connectFrom].on_enter, 30)
    : 'select source state';
  const fromNode = makePreviewNode(fromLabel, fromHint, 'from-node', state);
  fromNode.addEventListener('click', () => selectAndRerender('from-node'));
  container.appendChild(fromNode);

  const arrowIntentLabel = p.connectIntent || '???';
  const arrowEl = makePreviewArrow(arrowIntentLabel, p.intentKeywords, 'incoming-arrow', state);
  arrowEl.addEventListener('click', () => selectAndRerender('incoming-arrow'));
  container.appendChild(arrowEl);

  const newLabel = p.stateId ? p.stateId.toUpperCase() : 'NEW STATE';
  const newHint = p.onEnter ? truncate(p.onEnter, 30) : 'click to configure';
  const newNode = makePreviewNode(newLabel, newHint, 'new-state', state);
  newNode.addEventListener('click', () => selectAndRerender('new-state'));
  container.appendChild(newNode);

  const outgoing = document.createElement('div');
  outgoing.className = 'preview-outgoing-area';
  if (state.previewSelection === 'outgoing') outgoing.classList.add('preview-selected');
  const transCount = Object.keys(p.transitions).length;
  if (transCount > 0) {
    const summary = Object.entries(p.transitions)
      .map(([k, v]) => `${k} \u2192 ${v}`).join(', ');
    outgoing.textContent = truncate(summary, 40);
  } else {
    outgoing.textContent = '+ add transitions';
  }
  outgoing.addEventListener('click', () => selectAndRerender('outgoing'));
  container.appendChild(outgoing);
}

function makePreviewNode(
  label: string, hint: string,
  selection: PreviewSelection,
  state: AppState,
): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'wf-node editable';
  if (state.previewSelection === selection) div.classList.add('preview-selected');
  const idEl = document.createElement('div');
  idEl.className = 'node-id';
  idEl.textContent = label;
  const hintEl = document.createElement('div');
  hintEl.className = 'node-hint';
  hintEl.textContent = hint;
  div.appendChild(idEl);
  div.appendChild(hintEl);
  return div;
}

function makePreviewArrow(
  intentLabel: string, keywords: string[],
  selection: PreviewSelection,
  state: AppState,
): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'wf-arrow editable';
  if (state.previewSelection === selection) div.classList.add('preview-selected');

  const line = document.createElement('div');
  line.className = 'arrow-line';
  div.appendChild(line);

  const labelEl = document.createElement('div');
  labelEl.className = 'arrow-label';
  labelEl.textContent = intentLabel;
  div.appendChild(labelEl);

  if (keywords.length > 0) {
    const kw = document.createElement('div');
    kw.className = 'arrow-keywords';
    kw.textContent = keywords.slice(0, 3).map(k => `"${k}"`).join(', ');
    div.appendChild(kw);
  }

  const head = document.createElement('div');
  head.className = 'arrow-head';
  head.textContent = '\u25BC';
  div.appendChild(head);

  return div;
}

function makeEditorField(
  key: string, label: string, value: string, multiline: boolean,
  onChange: (val: string) => void,
): HTMLDivElement {
  const container = document.createElement('div');
  container.className = 'editor-field';
  const lbl = document.createElement('label');
  lbl.textContent = label;
  container.appendChild(lbl);

  if (multiline) {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.rows = Math.max(2, value.split('\n').length + 1);
    ta.dataset.fieldKey = key;
    ta.addEventListener('blur', () => onChange(ta.value));
    container.appendChild(ta);
  } else {
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = value;
    inp.dataset.fieldKey = key;
    inp.addEventListener('blur', () => onChange(inp.value));
    container.appendChild(inp);
  }
  return container;
}

function renderPreviewFields(
  def: WorkflowDef,
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  if (!state.addStatePreview) return;
  const p = state.addStatePreview;

  switch (state.previewSelection) {
    case 'from-node': {
      const selectorDiv = document.createElement('div');
      selectorDiv.className = 'state-selector';

      const existingIds = Object.keys(def.states);
      for (const sid of existingIds) {
        const btn = document.createElement('button');
        btn.className = 'state-selector-option';
        if (p.connectFrom === sid && !p.connectIntent) btn.classList.add('selected');
        btn.textContent = sid;
        btn.addEventListener('click', () => {
          p.connectFrom = sid;
          p.connectIntent = '';
          renderAddStatePanel(def, state, dom, cb);
        });
        selectorDiv.appendChild(btn);
      }

      for (const sid of existingIds) {
        const s = def.states[sid];
        for (const [intent, target] of Object.entries(s.transitions)) {
          if (intent === '*') continue;
          if (target === 'exit' || target.startsWith('exit:')) {
            const btn = document.createElement('button');
            btn.className = 'state-selector-option exit-option';
            const isSelected = p.connectFrom === sid && p.connectIntent === intent;
            if (isSelected) btn.classList.add('selected');
            btn.textContent = `${sid} / ${intent} \u2192 EXIT`;
            btn.addEventListener('click', () => {
              p.connectFrom = sid;
              p.connectIntent = intent;
              renderAddStatePanel(def, state, dom, cb);
            });
            selectorDiv.appendChild(btn);
          }
        }
      }

      dom.nodeEditor.appendChild(selectorDiv);
      break;
    }
    case 'incoming-arrow': {
      dom.nodeEditor.appendChild(makeEditorField(
        'connectIntent', 'Intent name', p.connectIntent, false,
        (val) => { p.connectIntent = val.trim(); refreshPreviewGraph(def, state, dom, cb); }
      ));
      dom.nodeEditor.appendChild(makeEditorField(
        'intentKeywords', 'Keywords (one per line)', p.intentKeywords.join('\n'), true,
        (val) => { p.intentKeywords = parseKeywords(val); refreshPreviewGraph(def, state, dom, cb); }
      ));
      break;
    }
    case 'new-state': {
      dom.nodeEditor.appendChild(makeEditorField(
        'stateId', 'State ID', p.stateId, false,
        (val) => {
          p.stateId = val.trim().toLowerCase().replace(/\s+/g, '_');
          refreshPreviewGraph(def, state, dom, cb);
        }
      ));
      dom.nodeEditor.appendChild(makeEditorField(
        'stepType', 'Step Type (llm / tool)', p.stepType, false,
        (val) => { p.stepType = val.trim() || 'llm'; }
      ));
      dom.nodeEditor.appendChild(makeEditorField(
        'onEnter', 'On Enter Message', p.onEnter, true,
        (val) => { p.onEnter = val; refreshPreviewGraph(def, state, dom, cb); }
      ));
      dom.nodeEditor.appendChild(makeEditorField(
        'systemPrompt', 'System Prompt', p.systemPrompt, true,
        (val) => { p.systemPrompt = val; }
      ));
      if (p.stepType === 'tool') {
        dom.nodeEditor.appendChild(makeEditorField(
          'toolNames', 'Tool Names (comma-separated)', p.toolNames.join(', '), false,
          (val) => { p.toolNames = val.split(',').map(s => s.trim()).filter(Boolean); }
        ));
      }
      break;
    }
    case 'outgoing': {
      const transStr = Object.entries(p.transitions)
        .map(([k, v]) => `${k} \u2192 ${v}`).join('\n');
      dom.nodeEditor.appendChild(makeEditorField(
        'transitions', 'Transitions (intent \u2192 target, one per line)', transStr, true,
        (val) => { p.transitions = parseTransitions(val); refreshPreviewGraph(def, state, dom, cb); }
      ));
      break;
    }
  }
}

function refreshPreviewGraph(def: WorkflowDef, state: AppState, dom: DomRefs, cb: AppCallbacks): void {
  const previewContainer = dom.nodeEditor.querySelector('.add-state-preview');
  if (!previewContainer) return;
  previewContainer.textContent = '';
  renderPreviewGraph(previewContainer as HTMLElement, def, state, dom, cb);
}

function applyAddState(
  def: WorkflowDef,
  state: AppState,
  cb: AppCallbacks,
): void {
  if (!state.addStatePreview) return;
  const p = state.addStatePreview;

  const stateId = p.stateId.trim().toLowerCase().replace(/\s+/g, '_');
  if (!stateId) { alert('State ID is required.'); return; }
  if (def.states[stateId]) { alert(`State "${stateId}" already exists.`); return; }

  const newState: WorkflowStateDef = {
    id: stateId,
    on_enter: p.onEnter || `Entered ${stateId}.`,
    step_type: p.stepType || 'llm',
    system_prompt: p.systemPrompt || '',
    tool_names: p.toolNames || [],
    narration: '',
    transitions: { ...p.transitions },
    handler: p.handler || undefined,
    state_fields: {},
    tool_args_map: {},
    auto_intent: p.stepType === 'tool' ? 'success' : '',
  };

  if (p.maxTurns != null) {
    newState.max_turns = p.maxTurns;
    if (p.maxTurnsTarget) newState.max_turns_target = p.maxTurnsTarget;
  }

  def.states[stateId] = newState;

  const connectFrom = p.connectFrom.trim();
  const connectIntent = p.connectIntent.trim();
  if (connectFrom && connectIntent && def.states[connectFrom]) {
    def.states[connectFrom].transitions[connectIntent] = stateId;
  }

  const keywords = p.intentKeywords;
  if (connectIntent && keywords.length > 0) {
    INTENT_SIGNALS[connectIntent] = keywords;
  }

  state.addStatePreview = null;
  cb.refreshAll();
  cb.closeEditor();
  cb.appendLog('system', `State "${stateId}" created.`);
}
