/**
 * workflowMap.ts — Renders the visual workflow graph (nodes, arrows, branches).
 * Extended with LLM/TOOL type badges and system prompt snippets on nodes.
 */

import type { WorkflowDef, WorkflowStateDef } from './workflow.js';
import type { AppState, AppCallbacks, ArrowContext } from './appContext.js';
import { escHtml, truncate, isSelfLoopTransition } from './domUtils.js';
import { attachZoomPan, type ZoomPanController } from './zoomPan.js';
import { connectDebugStream, type DebugEvent, type DebugStreamController } from './debugStream.js';
import { saveCheckpoint, loadSession, purgeExpired } from './extractedDataStore.js';

// Purge expired checkpoint data on module load
purgeExpired().catch(() => {});

export function renderWorkflowMap(
  def: WorkflowDef,
  state: AppState,
  workflowMapEl: HTMLElement,
  cb: AppCallbacks,
): void {
  workflowMapEl.textContent = '';
  const rendered = new Set<string>();

  // IDLE node
  workflowMapEl.appendChild(makeNode('idle', 'IDLE', `trigger: ${def.trigger_intent}`, true, cb));
  workflowMapEl.appendChild(makeArrow({ intent: def.trigger_intent }, def, cb));

  // Initial state
  const initial = def.states[def.initial_state];
  if (!initial) return;
  rendered.add(initial.id);
  const initialNode = makeNode(initial.id, initial.id.toUpperCase(), initial.on_enter || truncate(initial.system_prompt, 40), true, cb, initial.step_type, initial.state_fields);
  addSelfLoopBadge(initialNode, initial, cb);
  workflowMapEl.appendChild(initialNode);

  // Branch arms
  const transitionEntries = Object.entries(initial.transitions).filter(([k]) => k !== '*');
  if (transitionEntries.length > 0) {
    const branch = document.createElement('div');
    branch.className = 'wf-branch';

    for (const [intent, target] of transitionEntries) {
      const arm = document.createElement('div');
      arm.className = 'wf-branch-arm';
      arm.dataset.armIntent = intent;
      arm.dataset.armTarget = target.startsWith('exit:') ? 'exit' : target;
      arm.appendChild(makeArrow({ intent, fromStateId: initial.id, target }, def, cb));

      if (target === 'exit' || target.startsWith('exit:')) {
        const exitMsg = target.startsWith('exit:') ? target.slice(5) : def.exit_message;
        arm.appendChild(makeNode('exit-' + intent, 'EXIT', truncate(exitMsg, 40), true, cb));
      } else if (rendered.has(target)) {
        arm.appendChild(makeLinkTarget(target));
      } else {
        renderStateChain(arm, def, target, cb, rendered);
      }
      branch.appendChild(arm);
    }
    const vline = document.createElement('div');
    vline.className = 'branch-vline';
    workflowMapEl.appendChild(vline);

    workflowMapEl.appendChild(branch);
  }

  // Add State button
  const addBtn = document.createElement('button');
  addBtn.className = 'add-state-btn';
  addBtn.textContent = '+ ADD STATE';
  addBtn.addEventListener('click', () => cb.openAddStateEditor(def));
  workflowMapEl.appendChild(addBtn);

  requestAnimationFrame(() => {
    drawLinkConnectors(workflowMapEl);
    fixBranchConnectors(workflowMapEl);
  });
  observeResize(workflowMapEl);
}

function renderStateChain(
  container: HTMLElement,
  def: WorkflowDef,
  stateId: string,
  cb: AppCallbacks,
  rendered: Set<string>,
): void {
  const s = def.states[stateId];
  if (!s) return;
  rendered.add(stateId);

  const hint = s.handler ? `handler: ${s.handler}` : (s.on_enter || truncate(s.system_prompt, 40));
  const stateNode = makeNode(s.id, s.id.toUpperCase(), hint, true, cb, s.step_type, s.state_fields);
  addSelfLoopBadge(stateNode, s, cb);
  container.appendChild(stateNode);

  const transitions = Object.entries(s.transitions).filter(([k]) => k !== '*');
  if (transitions.length > 1) {
    const branch = document.createElement('div');
    branch.className = 'wf-branch';
    for (const [intent, target] of transitions) {
      const arm = document.createElement('div');
      arm.className = 'wf-branch-arm';
      arm.dataset.armIntent = intent;
      arm.dataset.armTarget = target.startsWith('exit:') ? 'exit' : target;
      arm.appendChild(makeArrow({ intent, fromStateId: stateId, target }, def, cb));
      if (target === 'exit' || target.startsWith('exit:')) {
        const exitMsg = target.startsWith('exit:') ? target.slice(5) : def.exit_message;
        arm.appendChild(makeNode('exit-' + stateId + '-' + intent, 'EXIT', truncate(exitMsg, 40), true, cb));
      } else if (rendered.has(target)) {
        arm.appendChild(makeLinkTarget(target));
      } else {
        renderStateChain(arm, def, target, cb, rendered);
      }
      branch.appendChild(arm);
    }
    const vline = document.createElement('div');
    vline.className = 'branch-vline';
    container.appendChild(vline);

    container.appendChild(branch);
  } else if (transitions.length === 1) {
    const [intent, target] = transitions[0];
    container.appendChild(makeArrow({ intent, fromStateId: stateId, target }, def, cb));
    if (target === 'exit' || target.startsWith('exit:')) {
      const exitMsg = target.startsWith('exit:') ? target.slice(5) : def.exit_message;
      container.appendChild(makeNode('exit-' + stateId, 'EXIT', truncate(exitMsg, 40), true, cb));
    } else if (rendered.has(target)) {
      container.appendChild(makeLinkTarget(target));
    } else {
      renderStateChain(container, def, target, cb, rendered);
    }
  } else {
    container.appendChild(makeArrow({ intent: 'exit phrase', isExitPhrase: true }, def, cb));
    container.appendChild(makeNode('exit-' + stateId, 'EXIT', truncate(def.exit_message, 40), true, cb));
  }
}

function makeNode(
  id: string, label: string, hint: string, editable: boolean, cb: AppCallbacks,
  stepType?: string,
  stateFields?: Record<string, string>,
): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'wf-node';
  div.dataset.node = id;
  if (id === 'exit' || id.startsWith('exit-')) div.classList.add('exit-node');
  if (editable) {
    div.classList.add('editable');
    div.addEventListener('click', () => {
      cb.highlightCodeBlock(id);
      cb.openEditor(id);
    });
  }

  // Step type badge (LLM/TOOL)
  if (stepType && id !== 'idle' && !id.startsWith('exit')) {
    const badge = document.createElement('span');
    badge.className = `step-type-badge step-type-${stepType}`;
    badge.textContent = stepType.toUpperCase();
    div.appendChild(badge);
  }

  const idEl = document.createElement('div');
  idEl.className = 'node-id';
  idEl.textContent = label;
  const hintEl = document.createElement('div');
  hintEl.className = 'node-hint';
  hintEl.textContent = truncate(hint, 50);
  div.appendChild(idEl);
  div.appendChild(hintEl);

  // Data field pills — show what this state collects
  if (stateFields && Object.keys(stateFields).length > 0) {
    const pillRow = document.createElement('div');
    pillRow.className = 'node-field-pills';
    for (const key of Object.keys(stateFields)) {
      const pill = document.createElement('span');
      pill.className = 'field-pill';
      pill.textContent = key;
      pillRow.appendChild(pill);
    }
    div.appendChild(pillRow);
  }

  return div;
}

function makeLinkTarget(stateId: string): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'link-target';
  div.dataset.linkTarget = stateId;
  div.textContent = `\u2197 ${stateId.toUpperCase()}`;
  div.addEventListener('click', () => {
    const real = document.querySelector(`.wf-node[data-node="${stateId}"]`);
    if (real) {
      real.scrollIntoView({ behavior: 'smooth', block: 'center' });
      real.classList.add('flash');
      real.addEventListener('animationend', () => real.classList.remove('flash'), { once: true });
    }
  });
  return div;
}

function makeArrow(ctx: ArrowContext, def: WorkflowDef, cb: AppCallbacks): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'wf-arrow editable';

  const line = document.createElement('div');
  line.className = 'arrow-line';
  div.appendChild(line);

  const labelEl = document.createElement('div');
  labelEl.className = 'arrow-label';
  labelEl.textContent = ctx.intent;
  div.appendChild(labelEl);

  if (ctx.isExitPhrase && def.exit_phrases.length > 0) {
    const kw = document.createElement('div');
    kw.className = 'arrow-keywords';
    kw.textContent = def.exit_phrases.slice(0, 3).map(k => `"${k}"`).join(', ');
    div.appendChild(kw);
  }

  const head = document.createElement('div');
  head.className = 'arrow-head';
  head.textContent = '\u25BC';
  div.appendChild(head);

  div.addEventListener('click', () => {
    cb.highlightCodeArrow(ctx.fromStateId, ctx.intent);
    cb.openArrowEditor(ctx);
  });
  return div;
}

let resizeObserver: ResizeObserver | null = null;

function observeResize(container: HTMLElement): void {
  if (resizeObserver) resizeObserver.disconnect();
  resizeObserver = new ResizeObserver(() => {
    drawLinkConnectors(container);
    fixBranchConnectors(container);
  });
  resizeObserver.observe(container);
  const panel = container.closest('.workflow-panel');
  if (panel) resizeObserver.observe(panel);
}

function drawLinkConnectors(container: HTMLElement): void {
  const old = container.querySelector('.link-connectors');
  if (old) old.remove();

  const targets = container.querySelectorAll('.link-target');
  if (targets.length === 0) return;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('link-connectors');
  svg.setAttribute('width', String(container.scrollWidth));
  svg.setAttribute('height', String(container.scrollHeight));
  container.appendChild(svg);

  const containerRect = container.getBoundingClientRect();

  targets.forEach(el => {
    const stateId = (el as HTMLElement).dataset.linkTarget;
    if (!stateId) return;
    const real = container.querySelector(`.wf-node[data-node="${stateId}"]`);
    if (!real) return;

    const elRect = el.getBoundingClientRect();
    const realRect = real.getBoundingClientRect();

    const scrollParent = container.closest('.workflow-panel');
    const scrollTop = scrollParent ? scrollParent.scrollTop : 0;
    const x1 = elRect.left - containerRect.left + elRect.width / 2;
    const y1 = elRect.top - containerRect.top + scrollTop + elRect.height / 2;
    const x2 = realRect.left - containerRect.left + realRect.width / 2;
    const y2 = realRect.top - containerRect.top + scrollTop + realRect.height / 2;

    const dx = Math.abs(x2 - x1) * 0.5;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M ${x1} ${y1} C ${x1 - dx} ${y1}, ${x2 + dx} ${y2}, ${x2} ${y2}`);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', '#4a9eff30');
    path.setAttribute('stroke-width', '1.5');
    path.setAttribute('stroke-dasharray', '4 3');
    svg.appendChild(path);
  });
}

/** Position horizontal branch connectors and add a centered label summary below the line. */
function fixBranchConnectors(container: HTMLElement): void {
  container.querySelectorAll('.wf-branch').forEach(branch => {
    // Remove previous connectors and labels
    branch.querySelector('.wf-branch-hline')?.remove();
    branch.querySelector('.branch-arm-labels')?.remove();

    const arms = branch.querySelectorAll(':scope > .wf-branch-arm');
    if (arms.length < 2) return;

    const branchRect = (branch as HTMLElement).getBoundingClientRect();
    const first = arms[0].getBoundingClientRect();
    const last = arms[arms.length - 1].getBoundingClientRect();

    // Center of first and last arm relative to branch
    const leftPos = first.left + first.width / 2 - branchRect.left;
    const rightPos = last.left + last.width / 2 - branchRect.left;

    const line = document.createElement('div');
    line.className = 'wf-branch-hline';
    line.style.left = `${leftPos}px`;
    line.style.width = `${rightPos - leftPos}px`;
    (branch as HTMLElement).appendChild(line);

    // Collect arm labels — flex layout spanning the line so dots sit at center
    const labelGroup = document.createElement('div');
    labelGroup.className = 'branch-arm-labels';
    labelGroup.style.left = `${leftPos}px`;
    labelGroup.style.width = `${rightPos - leftPos}px`;

    arms.forEach((arm, i) => {
      const armEl = arm as HTMLElement;
      const intent = armEl.dataset.armIntent;
      const target = armEl.dataset.armTarget;
      if (!intent) return;

      // Dot separator before all but the first label
      if (i > 0) {
        const dot = document.createElement('span');
        dot.className = 'branch-label-dot';
        dot.textContent = '·';
        labelGroup.appendChild(dot);
      }

      const label = document.createElement('span');
      label.className = 'branch-label-part';
      label.textContent = target && target !== 'exit'
        ? `(${intent} → ${target})`
        : `(${intent})`;
      labelGroup.appendChild(label);
    });

    (branch as HTMLElement).appendChild(labelGroup);
  });
}

// ── Fullscreen overlay ──────────────────────────────────────────────

export function openFullscreenMap(
  def: WorkflowDef,
  state: AppState,
  cb: AppCallbacks,
  sessionId?: string,
): void {
  const isLive = !!sessionId;

  // Overlay root
  const overlay = document.createElement('div');
  overlay.className = 'wf-overlay' + (isLive ? ' live-mode' : '');

  // Toolbar
  const toolbar = document.createElement('div');
  toolbar.className = 'wf-overlay-toolbar';

  const title = document.createElement('span');
  title.className = 'overlay-title';
  title.textContent = def.id.replace(/_/g, ' ');
  toolbar.appendChild(title);

  // Live mode toolbar additions
  let debugStream: DebugStreamController | null = null;
  let pausedBadge: HTMLElement | null = null;
  let isPaused = false;

  if (isLive && sessionId) {
    // Session ID badge
    const sessionBadge = document.createElement('span');
    sessionBadge.className = 'debug-session-badge';
    sessionBadge.textContent = sessionId;
    toolbar.appendChild(sessionBadge);

    // Pause/Resume button
    const pauseBtn = document.createElement('button');
    pauseBtn.className = 'debug-toolbar-btn';
    pauseBtn.textContent = 'PAUSE';
    pauseBtn.addEventListener('click', async () => {
      if (!debugStream) return;
      if (isPaused) {
        await debugStream.resume();
        isPaused = false;
        pauseBtn.textContent = 'PAUSE';
        pausedBadge?.remove();
        pausedBadge = null;
      } else {
        await debugStream.pause();
        isPaused = true;
        pauseBtn.textContent = 'RESUME';
        pausedBadge = document.createElement('span');
        pausedBadge.className = 'debug-paused-badge';
        pausedBadge.textContent = 'PAUSED';
        toolbar.insertBefore(pausedBadge, closeBtn);
      }
    });
    toolbar.appendChild(pauseBtn);

    // Copy Debug Context button
    const copyBtn = document.createElement('button');
    copyBtn.className = 'debug-toolbar-btn';
    copyBtn.textContent = 'COPY DEBUG';
    copyBtn.addEventListener('click', async () => {
      if (!debugStream) return;
      await debugStream.copyDebugContext();
      copyBtn.textContent = 'COPIED!';
      setTimeout(() => { copyBtn.textContent = 'COPY DEBUG'; }, 1500);
    });
    toolbar.appendChild(copyBtn);
  }

  const closeBtn = document.createElement('button');
  closeBtn.className = 'overlay-close';
  closeBtn.textContent = '\u00d7';
  toolbar.appendChild(closeBtn);
  overlay.appendChild(toolbar);

  // Main body: viewport + optional timeline panel
  const body = document.createElement('div');
  body.className = 'wf-overlay-body' + (isLive ? ' with-timeline' : '');

  // Viewport (scrollable area)
  const viewport = document.createElement('div');
  viewport.className = 'wf-overlay-canvas';

  // Content (transformed by zoom/pan)
  const content = document.createElement('div');
  content.className = 'wf-fullscreen';
  content.style.display = 'inline-flex';
  content.style.flexDirection = 'column';
  content.style.alignItems = 'center';
  viewport.appendChild(content);
  body.appendChild(viewport);

  // Right sidebar panel with tabs (live mode only)
  let timelineEl: HTMLElement | null = null;
  let conversationEl: HTMLElement | null = null;
  if (isLive) {
    const sidebar = document.createElement('div');
    sidebar.className = 'debug-sidebar';

    // Tab bar
    const tabBar = document.createElement('div');
    tabBar.className = 'debug-tab-bar';

    const eventsTab = document.createElement('button');
    eventsTab.className = 'debug-tab active';
    eventsTab.textContent = 'EVENTS';

    const convoTab = document.createElement('button');
    convoTab.className = 'debug-tab';
    convoTab.textContent = 'CONVERSATION';

    tabBar.appendChild(eventsTab);
    tabBar.appendChild(convoTab);
    sidebar.appendChild(tabBar);

    // Events panel
    timelineEl = document.createElement('div');
    timelineEl.className = 'debug-timeline';
    sidebar.appendChild(timelineEl);

    // Conversation panel
    conversationEl = document.createElement('div');
    conversationEl.className = 'debug-conversation';
    conversationEl.style.display = 'none';
    sidebar.appendChild(conversationEl);

    // Tab switching
    eventsTab.addEventListener('click', () => {
      eventsTab.classList.add('active');
      convoTab.classList.remove('active');
      timelineEl!.style.display = '';
      conversationEl!.style.display = 'none';
    });
    convoTab.addEventListener('click', () => {
      convoTab.classList.add('active');
      eventsTab.classList.remove('active');
      conversationEl!.style.display = '';
      timelineEl!.style.display = 'none';
    });

    body.appendChild(sidebar);
  }

  overlay.appendChild(body);

  // Mutable ref so onChange can call minimap update after it's created
  let onMinimapUpdate: (() => void) | null = null;
  let minimapCleanup: (() => void) | null = null;

  const controller = attachZoomPan(viewport, content, () => {
    onMinimapUpdate?.();
  });

  function close(): void {
    debugStream?.close();
    minimapCleanup?.();
    controller.destroy();
    overlay.remove();
    document.removeEventListener('keydown', onKey);
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') close();
  }

  closeBtn.addEventListener('click', close);
  document.addEventListener('keydown', onKey);

  // Wrap callbacks — close overlay first, then delegate
  const wrappedCb: AppCallbacks = {
    ...cb,
    openEditor: (nodeId) => { close(); cb.openEditor(nodeId); },
    openArrowEditor: (ctx) => { close(); cb.openArrowEditor(ctx); },
  };

  // Render graph into content
  renderWorkflowMap(def, state, content, wrappedCb);

  // Mount and fit — use setTimeout for live mode to let flex layout
  // settle with the timeline panel before measuring viewport dimensions
  document.body.appendChild(overlay);

  function initMinimap(): void {
    controller.fitToView();
    const mm = createMinimap(viewport, content, controller);
    onMinimapUpdate = mm.update;
    minimapCleanup = mm.destroy;
    // Initial viewport rect
    mm.update();
  }

  if (isLive) {
    setTimeout(initMinimap, 50);
  } else {
    requestAnimationFrame(() => {
      requestAnimationFrame(initMinimap);
    });
  }

  // Connect debug stream (live mode)
  if (isLive && sessionId && timelineEl) {
    let startTime: number | null = null;
    let activeStateId = '';

    // Data checkpoint panel — accumulates extracted fields across all states
    const checkpointPanel = createDataCheckpointPanel(sessionId);
    viewport.appendChild(checkpointPanel.element);

    // Helper: highlight a node as the active state and zoom to it
    function activateNode(stateId: string, zoom: boolean): void {
      if (!stateId || stateId === activeStateId) return;
      activeStateId = stateId;
      content.querySelectorAll('.wf-node.session-active')
        .forEach(n => n.classList.remove('session-active', 'node-pulse'));
      content.querySelectorAll(`[data-node="${stateId}"]`)
        .forEach(n => {
          n.classList.add('session-active', 'node-pulse');
          n.addEventListener('animationend', () => n.classList.remove('node-pulse'), { once: true });
        });
      if (zoom) controller.zoomToNode(stateId);
    }

    // Helper: light up field pills when data is extracted
    function highlightExtractedFields(data: Record<string, unknown>): void {
      const activeNode = content.querySelector('.wf-node.session-active');
      if (!activeNode) return;
      for (const key of Object.keys(data)) {
        activeNode.querySelectorAll('.field-pill').forEach(pill => {
          if (pill.textContent === key) {
            pill.classList.add('field-pill-filled');
          }
        });
      }
    }

    debugStream = connectDebugStream(
      sessionId,
      (event: DebugEvent) => {
        if (startTime === null) startTime = event.timestamp;
        appendTimelineEvent(timelineEl!, event, startTime);

        // Append to conversation transcript for human/AI turns
        if (conversationEl) {
          if (event.type === 'stt') {
            appendConversationTurn(conversationEl, 'human', String(event.data.text ?? ''));
          } else if (event.type === 'llm_response') {
            // Strip JSON blocks from displayed response
            const raw = String(event.data.response ?? '');
            const clean = raw
              .replace(/```(?:json)?\s*\n?\{.*?\}\s*\n?```/gs, '')
              .replace(/^\s*\{[^}]*"intent"[^}]*\}\s*$/gm, '')
              .trim();
            if (clean) appendConversationTurn(conversationEl, 'ai', clean);
          }
        }

        // Always track the current state — highlight on first event
        // and zoom+pan on transitions
        const currentState = event.state_id || '';
        if (event.type === 'transition') {
          const toState = String(event.data.to ?? '');
          activateNode(toState, true);
        } else if (activeStateId === '') {
          // First event received — highlight current state immediately
          activateNode(currentState, true);
        }

        // Light up field pills incrementally as data is gathered
        if (event.type === 'field_progress' && event.data.fields) {
          const fields = event.data.fields as Record<string, unknown>;
          highlightExtractedFields(fields);
          checkpointPanel.addData(event.state_id || activeStateId, fields);
        }

        // Also light up on step_complete (final extraction)
        if (event.type === 'step_complete' && event.data.extracted_data) {
          const extracted = event.data.extracted_data as Record<string, unknown>;
          highlightExtractedFields(extracted);
          checkpointPanel.addData(event.state_id || activeStateId, extracted);
        }
      },
      () => {
        // On disconnect, show indicator
        if (timelineEl) {
          const disc = document.createElement('div');
          disc.className = 'debug-event type-error';
          disc.textContent = 'Stream disconnected';
          timelineEl.appendChild(disc);
        }
      },
    );
  }
}

// ── Minimap ─────────────────────────────────────────────────────────

interface MinimapHandle {
  update(): void;
  destroy(): void;
}

function createMinimap(
  viewport: HTMLElement,
  content: HTMLElement,
  controller: ZoomPanController,
): MinimapHandle {
  const MINIMAP_W = 220;
  const MINIMAP_H = 150;

  // Container
  const minimap = document.createElement('div');
  minimap.className = 'minimap';

  // Inner wrapper for scaled clone
  const minimapContent = document.createElement('div');
  minimapContent.className = 'minimap-content';
  minimap.appendChild(minimapContent);

  // Clone the rendered graph
  const clone = content.cloneNode(true) as HTMLElement;
  clone.style.transform = 'none';
  clone.style.transformOrigin = '0 0';
  minimapContent.appendChild(clone);

  // Viewport indicator rectangle
  const viewportRect = document.createElement('div');
  viewportRect.className = 'minimap-viewport';
  minimap.appendChild(viewportRect);

  // Measure content bounds to compute minimap scale
  let contentMinX = 0;
  let contentMinY = 0;
  let contentW = 0;
  let contentH = 0;
  let miniScale = 1;

  function measureAndScale(): void {
    // Temporarily reset clone transform to measure natural size
    clone.style.transform = 'none';

    const origin = clone.getBoundingClientRect();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    clone.querySelectorAll('.wf-node, .wf-arrow, .link-target, .add-state-btn').forEach(child => {
      const r = child.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      minX = Math.min(minX, r.left);
      minY = Math.min(minY, r.top);
      maxX = Math.max(maxX, r.right);
      maxY = Math.max(maxY, r.bottom);
    });

    if (!isFinite(minX)) return;

    contentMinX = minX - origin.left;
    contentMinY = minY - origin.top;
    contentW = maxX - minX;
    contentH = maxY - minY;

    if (contentW === 0 || contentH === 0) return;

    const margin = 0.9;
    miniScale = Math.min(MINIMAP_W / contentW, MINIMAP_H / contentH) * margin;

    const offsetX = -contentMinX + (MINIMAP_W / miniScale - contentW) / 2;
    const offsetY = -contentMinY + (MINIMAP_H / miniScale - contentH) / 2;

    clone.style.transformOrigin = '0 0';
    clone.style.transform = `scale(${miniScale}) translate(${offsetX}px, ${offsetY}px)`;
  }

  function update(): void {
    const { tx, ty, scale } = controller.getTransform();
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;

    // Visible content rectangle in content-local coords
    const visibleLeft = -tx / scale;
    const visibleTop = -ty / scale;
    const visibleW = vw / scale;
    const visibleH = vh / scale;

    // Convert to minimap pixel coords
    const offsetX = (MINIMAP_W / miniScale - contentW) / 2;
    const offsetY = (MINIMAP_H / miniScale - contentH) / 2;

    const rectLeft = (visibleLeft - contentMinX) * miniScale + offsetX * miniScale;
    const rectTop = (visibleTop - contentMinY) * miniScale + offsetY * miniScale;
    const rectW = visibleW * miniScale;
    const rectH = visibleH * miniScale;

    viewportRect.style.left = `${rectLeft}px`;
    viewportRect.style.top = `${rectTop}px`;
    viewportRect.style.width = `${rectW}px`;
    viewportRect.style.height = `${rectH}px`;
  }

  // Click-to-navigate
  function onClick(e: MouseEvent): void {
    const rect = minimap.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    // Convert minimap click to content-space coords
    const offsetX = (MINIMAP_W / miniScale - contentW) / 2;
    const offsetY = (MINIMAP_H / miniScale - contentH) / 2;

    const contentX = (clickX / miniScale) - offsetX + contentMinX;
    const contentY = (clickY / miniScale) - offsetY + contentMinY;

    // Center that content point in the main viewport
    const { scale } = controller.getTransform();
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    const newTx = vw / 2 - contentX * scale;
    const newTy = vh / 2 - contentY * scale;

    controller.setTransform(newTx, newTy, scale);
  }

  minimap.addEventListener('click', onClick);

  // Mount and measure
  viewport.appendChild(minimap);
  requestAnimationFrame(() => measureAndScale());

  function destroy(): void {
    minimap.removeEventListener('click', onClick);
    minimap.remove();
  }

  return { update, destroy };
}

// ── Data Checkpoint Panel ────────────────────────────────────────────

interface DataCheckpointHandle {
  element: HTMLElement;
  addData(stateId: string, data: Record<string, unknown>): void;
}

function createDataCheckpointPanel(sessionId?: string): DataCheckpointHandle {
  const panel = document.createElement('div');
  panel.className = 'data-checkpoint-panel';

  const header = document.createElement('div');
  header.className = 'data-checkpoint-header';
  header.textContent = 'EXTRACTED DATA';
  panel.appendChild(header);

  const body = document.createElement('div');
  body.className = 'data-checkpoint-body';
  panel.appendChild(body);

  const empty = document.createElement('div');
  empty.className = 'data-checkpoint-empty';
  empty.textContent = 'Waiting for data extraction...';
  body.appendChild(empty);

  // Track groups by state so we append new fields to existing groups
  const groups = new Map<string, HTMLElement>();

  function renderRow(group: HTMLElement, key: string, value: unknown): void {
    // Remove existing row for this key if re-extracted
    const existing = group.querySelector(`[data-key="${key}"]`);
    if (existing) existing.remove();

    const row = document.createElement('div');
    row.className = 'data-checkpoint-row';
    row.dataset.key = key;

    const keyEl = document.createElement('span');
    keyEl.className = 'data-checkpoint-key';
    keyEl.textContent = key;

    const valEl = document.createElement('span');
    valEl.className = 'data-checkpoint-value';
    valEl.textContent = String(value);

    row.appendChild(keyEl);
    row.appendChild(valEl);
    group.appendChild(row);
  }

  function getOrCreateGroup(stateId: string): HTMLElement {
    let group = groups.get(stateId);
    if (!group) {
      // Remove empty placeholder
      const emptyEl = body.querySelector('.data-checkpoint-empty');
      if (emptyEl) emptyEl.remove();

      group = document.createElement('div');
      group.className = 'data-checkpoint-group';
      const stateLabel = document.createElement('div');
      stateLabel.className = 'data-checkpoint-state';
      stateLabel.textContent = stateId.replace(/_/g, ' ');
      group.appendChild(stateLabel);
      body.appendChild(group);
      groups.set(stateId, group);
    }
    return group;
  }

  function addData(stateId: string, data: Record<string, unknown>): void {
    const group = getOrCreateGroup(stateId);

    for (const [key, value] of Object.entries(data)) {
      if (key === 'intent' || key === 'done') continue;
      if (value === null || value === undefined) continue;
      renderRow(group, key, value);
    }

    body.scrollTop = body.scrollHeight;

    // Persist to IndexedDB
    if (sessionId) {
      saveCheckpoint(sessionId, stateId, data).catch(() => {});
    }
  }

  // Restore previously persisted data for this session
  if (sessionId) {
    loadSession(sessionId).then((stored) => {
      for (const [stateId, fields] of stored) {
        const group = getOrCreateGroup(stateId);
        for (const [key, value] of Object.entries(fields)) {
          renderRow(group, key, value);
        }
      }
    }).catch(() => {});
  }

  return { element: panel, addData };
}

function appendTimelineEvent(
  container: HTMLElement,
  event: DebugEvent,
  startTime: number,
): void {
  const entry = document.createElement('div');
  entry.className = `debug-event type-${event.type}`;

  const dt = (event.timestamp - startTime).toFixed(1);
  const time = document.createElement('span');
  time.className = 'debug-event-time';
  time.textContent = `${dt}s`;

  const badge = document.createElement('span');
  badge.className = 'debug-event-type';
  badge.textContent = event.type.toUpperCase();

  const text = document.createElement('span');
  text.className = 'debug-event-text';
  text.textContent = formatDebugEventText(event);

  entry.appendChild(time);
  entry.appendChild(badge);
  entry.appendChild(text);
  container.appendChild(entry);
  container.scrollTop = container.scrollHeight;
}

function formatDebugEventText(ev: DebugEvent): string {
  const d = ev.data;
  switch (ev.type) {
    case 'transition':
      return `${d.from} \u2192 ${d.to} (${d.intent})`;
    case 'llm_call':
      return String(d.user_text ?? '').slice(0, 50);
    case 'llm_response':
      return String(d.response ?? '').slice(0, 50);
    case 'tool_exec':
      return `${d.tool_name}: ${String(d.result ?? '').slice(0, 40)}`;
    case 'stt':
      return `"${String(d.text ?? '').slice(0, 50)}"`;
    case 'step_complete':
      return JSON.stringify(d.extracted_data ?? {}).slice(0, 60);
    case 'field_progress': {
      const fields = d.fields as Record<string, unknown> | undefined;
      if (!fields) return '';
      return Object.entries(fields).map(([k, v]) => `${k}=${v}`).join(', ');
    }
    case 'pause':
      return 'Session paused';
    case 'resume':
      return 'Session resumed';
    default:
      return JSON.stringify(d).slice(0, 60);
  }
}

function appendConversationTurn(
  container: HTMLElement,
  role: 'human' | 'ai',
  text: string,
): void {
  const turn = document.createElement('div');
  turn.className = `convo-turn convo-${role}`;

  const label = document.createElement('div');
  label.className = 'convo-label';
  label.textContent = role === 'human' ? 'CALLER' : 'ASSISTANT';

  const body = document.createElement('div');
  body.className = 'convo-text';
  body.textContent = text;

  turn.appendChild(label);
  turn.appendChild(body);
  container.appendChild(turn);
  container.scrollTop = container.scrollHeight;
}

function addSelfLoopBadge(nodeEl: HTMLDivElement, s: WorkflowStateDef, cb: AppCallbacks): void {
  const wildcard = s.transitions['*'];
  if (!wildcard || !isSelfLoopTransition(wildcard, s.id)) return;
  const maxLabel = s.max_turns ? ` (max ${s.max_turns})` : '';
  const badge = document.createElement('span');
  badge.className = 'self-loop-badge';
  badge.textContent = `\u21BB *${maxLabel}`;
  badge.addEventListener('click', (e) => {
    e.stopPropagation();
    cb.openArrowEditor({ intent: '*', fromStateId: s.id, target: wildcard });
  });
  nodeEl.appendChild(badge);
}
