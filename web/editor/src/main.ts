/**
 * main.ts — Entry point for the scheduling workflow editor.
 * Fetches workflow from API, initializes all editor modules.
 */

import type { WorkflowDef } from './workflow.js';
import { createAppState, type DomRefs, type AppCallbacks } from './appContext.js';
import { renderWorkflowMap, openFullscreenMap } from './workflowMap.js';
import { openEditor } from './nodeEditor.js';
import { openArrowEditor } from './arrowEditor.js';
import { openAddStateEditor } from './addStateEditor.js';
import { refreshCodeView, highlightCodeBlock, highlightCodeArrow } from './codeHighlight.js';
import { fetchWorkflow, saveWorkflow } from './api.js';
import { startSessionMonitor } from './sessionMonitor.js';

// ── DOM refs ────────────────────────────────────────────────────────

const dom: DomRefs = {
  workflowMapEl: document.getElementById('workflow-map')!,
  nodeEditor: document.getElementById('node-editor')!,
  layoutEl: document.querySelector('.layout')!,
  codeViewEl: document.getElementById('code-view')!,
  eventLog: document.getElementById('event-log')!,
  sessionMonitor: document.getElementById('session-monitor')!,
  modeBadge: document.getElementById('mode-badge')!,
};

// ── Shared state ────────────────────────────────────────────────────

const state = createAppState();

// ── Callback bag ─────────────────────────────────────────────────────

function closeEditor(): void {
  dom.nodeEditor.classList.remove('open');
  dom.layoutEl.classList.remove('editor-open');
  dom.nodeEditor.textContent = '';
  document.querySelectorAll('.wf-node.editing').forEach(n => n.classList.remove('editing'));
  document.querySelectorAll('.wf-arrow.editing').forEach(n => n.classList.remove('editing'));
  state.editingNodeId = null;
}

function refreshAll(): void {
  if (!state.currentDef) return;
  renderWorkflowMap(state.currentDef, state, dom.workflowMapEl, cb);
  refreshCodeView(state, dom, cb);
}

function appendLog(type: string, text: string): void {
  const entry = document.createElement('div');
  entry.className = 'log-entry';

  const time = document.createElement('span');
  time.className = 'log-time';
  time.textContent = new Date().toLocaleTimeString();

  const msg = document.createElement('span');
  msg.className = `log-event type-${type}`;
  msg.textContent = text;

  entry.appendChild(time);
  entry.appendChild(msg);
  dom.eventLog.appendChild(entry);
  dom.eventLog.scrollTop = dom.eventLog.scrollHeight;
}

const cb: AppCallbacks = {
  openEditor: (nodeId) => openEditor(nodeId, state, dom, cb),
  openArrowEditor: (ctx) => openArrowEditor(ctx, state, dom, cb),
  openAddStateEditor: (def) => openAddStateEditor(def, state, dom, cb),
  closeEditor,
  refreshAll,
  highlightCodeBlock,
  highlightCodeArrow,
  appendLog,
  openLiveDebug: (sessionId) => {
    if (state.currentDef) openFullscreenMap(state.currentDef, state, cb, sessionId);
  },
};

// ── Panel toggles ───────────────────────────────────────────────────

document.querySelectorAll('.panel-toggle').forEach(btn => {
  btn.addEventListener('click', () => {
    const panel = (btn as HTMLElement).dataset.panel;
    if (!panel) return;
    dom.layoutEl.classList.toggle(`${panel}-closed`);
    btn.classList.toggle('active');
  });
});

document.querySelectorAll('.panel-close').forEach(btn => {
  btn.addEventListener('click', () => {
    const panel = (btn as HTMLElement).dataset.panel;
    if (!panel) return;
    dom.layoutEl.classList.add(`${panel}-closed`);
    const toggle = document.querySelector(`.panel-toggle[data-panel="${panel}"]`);
    toggle?.classList.remove('active');
  });
});

// ── Resizable columns ───────────────────────────────────────────────

let mapWidth = 340;
let sessionWidth = 280;
const layout = dom.layoutEl as HTMLElement;
const handleL = layout.querySelector('.resize-handle[data-handle="left"]') as HTMLElement;
const handleR = layout.querySelector('.resize-handle[data-handle="right"]') as HTMLElement;

function updateGrid(): void {
  const m = !layout.classList.contains('map-closed');
  const c = !layout.classList.contains('code-closed') || layout.classList.contains('editor-open');
  const s = !layout.classList.contains('session-closed');

  let template: string;
  let showHL = false;
  let showHR = false;

  if (m && c && s) {
    template = `${mapWidth}px 4px 1fr 4px ${sessionWidth}px`;
    showHL = true; showHR = true;
  } else if (m && c && !s) {
    template = `${mapWidth}px 4px 1fr`;
    showHL = true;
  } else if (m && !c && s) {
    template = `1fr 4px ${sessionWidth}px`;
    showHL = true;
  } else if (!m && c && s) {
    template = `1fr 4px ${sessionWidth}px`;
    showHR = true;
  } else {
    template = '1fr';
  }

  if (handleL) handleL.style.display = showHL ? '' : 'none';
  if (handleR) handleR.style.display = showHR ? '' : 'none';
  layout.style.gridTemplateColumns = template;
}

new MutationObserver(() => updateGrid()).observe(layout, {
  attributes: true, attributeFilter: ['class'],
});
updateGrid();

let dragTarget: 'map' | 'session' | null = null;
let dragStartX = 0;
let dragStartWidth = 0;

function onHandleDown(handle: 'left' | 'right', e: MouseEvent): void {
  e.preventDefault();
  if (handle === 'left') {
    dragTarget = 'map';
    dragStartWidth = mapWidth;
  } else {
    dragTarget = 'session';
    dragStartWidth = sessionWidth;
  }
  dragStartX = e.clientX;
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
}

function onMouseMove(e: MouseEvent): void {
  if (!dragTarget) return;
  const dx = e.clientX - dragStartX;
  const MIN = 160;
  const MAX = Math.floor(layout.offsetWidth * 0.6);

  if (dragTarget === 'map') {
    mapWidth = Math.max(MIN, Math.min(MAX, dragStartWidth + dx));
  } else {
    sessionWidth = Math.max(MIN, Math.min(MAX, dragStartWidth - dx));
  }
  updateGrid();
}

function onMouseUp(): void {
  if (!dragTarget) return;
  dragTarget = null;
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
}

if (handleL) handleL.addEventListener('mousedown', (e) => onHandleDown('left', e));
if (handleR) handleR.addEventListener('mousedown', (e) => onHandleDown('right', e));
document.addEventListener('mousemove', onMouseMove);
document.addEventListener('mouseup', onMouseUp);

// ── Expand map button ────────────────────────────────────────────────

document.getElementById('expand-map')?.addEventListener('click', () => {
  if (state.currentDef) openFullscreenMap(state.currentDef, state, cb);
});

// ── Save button ──────────────────────────────────────────────────────

document.getElementById('save-btn')?.addEventListener('click', async () => {
  if (!state.currentDef) return;
  try {
    await saveWorkflow(state.currentDef.id, state.currentDef);
    appendLog('system', 'Workflow saved to server.');
  } catch (err) {
    appendLog('system', `Save failed: ${err}`);
  }
});

// ── Session monitor: highlight active state in graph ─────────────────

function highlightActiveState(stateId: string | null): void {
  document.querySelectorAll('.wf-node.session-active')
    .forEach(n => n.classList.remove('session-active'));
  if (stateId) {
    document.querySelectorAll(`[data-node="${stateId}"]`)
      .forEach(n => n.classList.add('session-active'));
  }
}

// ── Boot ────────────────────────────────────────────────────────────

async function init(): Promise<void> {
  try {
    const def = await fetchWorkflow('apartment_viewing');
    state.currentDef = def;
    renderWorkflowMap(def, state, dom.workflowMapEl, cb);
    refreshCodeView(state, dom, cb);
    appendLog('system', `Workflow "${def.id}" loaded (${Object.keys(def.states).length} states).`);
  } catch (err) {
    appendLog('system', `Failed to load workflow: ${err}`);
  }

  startSessionMonitor(dom.sessionMonitor, highlightActiveState, cb);
}

init();
