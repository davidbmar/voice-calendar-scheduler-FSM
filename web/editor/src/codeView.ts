/**
 * codeView.ts — Generates color-coded pseudocode DOM from a WorkflowDef.
 * Extended with stepType, systemPrompt, and toolNames rendering.
 *
 * Note: innerHTML is used here for syntax-highlighted code rendering.
 * All content is escaped via escHtml() and comes from internal workflow
 * definitions, not external user input.
 */

import type { WorkflowDef, WorkflowStateDef } from './workflow.js';

function escHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function kw(text: string): string {
  return `<span class="code-keyword">${text}</span>`;
}

function str(text: string): string {
  return `<span class="code-string">"${escHtml(text)}"</span>`;
}

function intent(text: string): string {
  return `<span class="code-intent">${escHtml(text)}</span>`;
}

function stateRef(text: string): string {
  return `<span class="code-state-ref">${escHtml(text.toUpperCase())}</span>`;
}

function arrow(): string {
  return `<span class="code-arrow">\u2192</span>`;
}

function renderTransitionLine(stateId: string, intentName: string, target: string): string {
  const colonIdx = target.indexOf(':');
  const isExit = target === 'exit' || target.startsWith('exit:');
  const isSelfLoop = target === stateId || (colonIdx !== -1 && target.slice(0, colonIdx) === stateId);

  let action: string;
  if (isExit) {
    const msg = target.startsWith('exit:') ? target.slice(5) : '';
    action = msg ? `${kw('exit')} ${str(msg)}` : kw('exit');
  } else if (isSelfLoop) {
    const msg = colonIdx !== -1 ? target.slice(colonIdx + 1) : '';
    action = msg ? `${kw('retry')} ${str(msg)}` : kw('retry');
  } else {
    const targetState = colonIdx !== -1 ? target.slice(0, colonIdx) : target;
    const msg = colonIdx !== -1 ? target.slice(colonIdx + 1) : '';
    action = msg
      ? `${kw('goto')} ${stateRef(targetState)} ${str(msg)}`
      : `${kw('goto')} ${stateRef(targetState)}`;
  }

  return `<div class="code-line" data-code-arrow="${escHtml(stateId)}:${escHtml(intentName)}">${kw('on')} ${intent(intentName)} ${arrow()} ${action}</div>`;
}

function renderStateBlock(state: WorkflowStateDef): string {
  const lines: string[] = [];

  // Step type badge
  const typeBadge = state.step_type === 'tool'
    ? `<span class="code-tool-badge">TOOL</span>`
    : `<span class="code-llm-badge">LLM</span>`;
  lines.push(`<div class="code-line">${typeBadge}</div>`);

  if (state.on_enter) {
    lines.push(`<div class="code-line">${kw('say')} ${str(state.on_enter)}</div>`);
  }

  if (state.tool_names.length > 0) {
    lines.push(`<div class="code-line">${kw('tools:')} ${state.tool_names.map(t => intent(t)).join(', ')}</div>`);
  }

  if (state.system_prompt) {
    const snippet = state.system_prompt.split('\n')[0].slice(0, 60);
    lines.push(`<div class="code-line">${kw('prompt:')} ${str(snippet + (state.system_prompt.length > 60 ? '...' : ''))}</div>`);
  }

  if (state.handler) {
    lines.push(`<div class="code-line">${kw('capture:')} ${escHtml(state.handler)}</div>`);
  }

  if (state.max_turns != null) {
    const target = state.max_turns_target ?? 'exit';
    const isExit = target === 'exit' || target.startsWith('exit:');
    const msg = target.startsWith('exit:') ? target.slice(5) : '';
    const act = isExit
      ? (msg ? `${kw('exit')} ${str(msg)}` : kw('exit'))
      : `${kw('goto')} ${stateRef(target)}`;
    lines.push(`<div class="code-line">${kw('max')} ${state.max_turns} turns ${arrow()} ${act}</div>`);
  }

  for (const [intentName, target] of Object.entries(state.transitions)) {
    lines.push(renderTransitionLine(state.id, intentName, target));
  }

  return lines.join('\n');
}

// All values passed to innerHTML are escaped through escHtml() above.
// Content originates from internal workflow definitions, not external input.
/* eslint-disable no-unsanitized/property */

export function renderCodeView(
  def: WorkflowDef,
  container: HTMLElement,
  onClickBlock: (nodeId: string) => void
): void {
  container.textContent = '';

  const headerBlock = document.createElement('div');
  headerBlock.className = 'code-block';
  headerBlock.dataset.codeFor = '__workflow__';
  const exitList = def.exit_phrases.slice(0, 3).map(p => `"${escHtml(p)}"`).join(', ');
  const exitEllipsis = def.exit_phrases.length > 3 ? ', ...' : '';
  headerBlock.innerHTML = [
    `<div class="code-line">${kw('workflow')} ${str(def.id)}</div>`,
    `<div class="code-indent">`,
    `  <div class="code-line">${kw('trigger:')} ${intent(def.trigger_intent)}</div>`,
    `  <div class="code-line">${kw('exit on:')} ${exitList}${exitEllipsis}</div>`,
    `  <div class="code-line">${kw('exit says:')} ${str(def.exit_message)}</div>`,
    `</div>`,
  ].join('\n');
  headerBlock.addEventListener('click', () => onClickBlock('idle'));
  container.appendChild(headerBlock);

  const rendered = new Set<string>();
  const queue = [def.initial_state];

  while (queue.length > 0) {
    const stateId = queue.shift()!;
    if (rendered.has(stateId)) continue;
    rendered.add(stateId);

    const s = def.states[stateId];
    if (!s) continue;

    const block = document.createElement('div');
    block.className = 'code-block';
    block.dataset.codeFor = stateId;
    block.innerHTML = [
      `<div class="code-line code-state-header">${kw('state')} ${stateRef(stateId)}</div>`,
      `<div class="code-indent">`,
      renderStateBlock(s),
      `</div>`,
    ].join('\n');
    block.addEventListener('click', () => onClickBlock(stateId));
    container.appendChild(block);

    for (const target of Object.values(s.transitions)) {
      const colonIdx = target.indexOf(':');
      const targetId = colonIdx !== -1 ? target.slice(0, colonIdx) : target;
      if (targetId !== 'exit' && def.states[targetId] && !rendered.has(targetId)) {
        queue.push(targetId);
      }
    }
  }

  for (const stateId of Object.keys(def.states)) {
    if (rendered.has(stateId)) continue;
    rendered.add(stateId);
    const s = def.states[stateId];
    const block = document.createElement('div');
    block.className = 'code-block';
    block.dataset.codeFor = stateId;
    block.innerHTML = [
      `<div class="code-line code-state-header">${kw('state')} ${stateRef(stateId)}</div>`,
      `<div class="code-indent">`,
      renderStateBlock(s),
      `</div>`,
    ].join('\n');
    block.addEventListener('click', () => onClickBlock(stateId));
    container.appendChild(block);
  }
}
