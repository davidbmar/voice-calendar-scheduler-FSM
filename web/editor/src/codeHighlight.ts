/**
 * codeHighlight.ts — Code view refresh and bidirectional highlighting helpers.
 */

import type { AppState, DomRefs, AppCallbacks } from './appContext.js';
import { renderCodeView } from './codeView.js';

export function refreshCodeView(state: AppState, dom: DomRefs, cb: AppCallbacks): void {
  if (!state.currentDef) return;
  renderCodeView(state.currentDef, dom.codeViewEl, (nodeId) => cb.openEditor(nodeId));
}

export function highlightCodeBlock(nodeId: string): void {
  document.querySelectorAll('.code-block.code-active')
    .forEach(b => b.classList.remove('code-active'));
  document.querySelectorAll('.code-line.code-active')
    .forEach(b => b.classList.remove('code-active'));
  const block = document.querySelector(`[data-code-for="${nodeId}"]`);
  block?.classList.add('code-active');
  block?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

export function highlightCodeArrow(fromState: string | undefined, intent: string): void {
  document.querySelectorAll('.code-block.code-active')
    .forEach(b => b.classList.remove('code-active'));
  document.querySelectorAll('.code-line.code-active')
    .forEach(b => b.classList.remove('code-active'));
  if (!fromState) return;
  const line = document.querySelector(`[data-code-arrow="${fromState}:${intent}"]`);
  line?.classList.add('code-active');
  const block = line?.closest('.code-block');
  block?.classList.add('code-active');
  line?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
