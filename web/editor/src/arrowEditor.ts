/**
 * arrowEditor.ts — Click-to-edit panel for workflow arrows (transitions).
 */

import type { AppState, DomRefs, AppCallbacks, ArrowContext } from './appContext.js';
import { INTENT_SIGNALS } from './intentClassifier.js';
import { parseKeywords, parseExitPhrasesNewline } from './editorUtils.js';
import type { EditorField } from './nodeEditor.js';

export function openArrowEditor(
  ctx: ArrowContext,
  state: AppState,
  dom: DomRefs,
  cb: AppCallbacks,
): void {
  if (!state.currentDef) return;

  document.querySelectorAll('.wf-node.editing').forEach(n => n.classList.remove('editing'));
  document.querySelectorAll('.wf-arrow.editing').forEach(n => n.classList.remove('editing'));
  state.editingNodeId = `arrow:${ctx.intent}`;

  dom.nodeEditor.textContent = '';
  dom.nodeEditor.classList.add('open');
  dom.layoutEl.classList.add('editor-open');

  if (ctx.isExitPhrase) {
    renderArrowEditorFields('Exit Phrases', [
      { key: 'exitPhrases', label: 'Exit Phrases (one per line)', value: state.currentDef.exit_phrases.join('\n'), multiline: true },
    ], ctx, state, dom, cb);
    return;
  }

  const fields: EditorField[] = [];

  const signals = INTENT_SIGNALS[ctx.intent] ?? [];
  fields.push({
    key: 'keywords',
    label: `Keywords for "${ctx.intent}" (one per line)`,
    value: signals.join('\n'),
    multiline: true,
  });

  if (ctx.fromStateId && ctx.target) {
    const isExit = ctx.target === 'exit' || ctx.target.startsWith('exit:');
    const targetDisplay = isExit ? 'exit' : ctx.target;
    fields.push({
      key: 'target',
      label: `Target state (currently: ${targetDisplay})`,
      value: targetDisplay,
    });
    if (isExit) {
      const exitMsg = ctx.target.startsWith('exit:') ? ctx.target.slice(5) : state.currentDef.exit_message;
      fields.push({
        key: 'exitMessage',
        label: 'Exit message for this transition',
        value: exitMsg,
        multiline: true,
      });
    }
  }

  const targetLabel = ctx.target
    ? (ctx.target === 'exit' || ctx.target.startsWith('exit:') ? 'EXIT' : ctx.target)
    : '?';
  const title = ctx.fromStateId
    ? `Transition: ${ctx.fromStateId} \u2192 [${ctx.intent}] \u2192 ${targetLabel}`
    : `Trigger: ${ctx.intent}`;

  renderArrowEditorFields(title, fields, ctx, state, dom, cb);
}

function renderArrowEditorFields(
  title: string,
  fields: EditorField[],
  ctx: ArrowContext,
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
  saveBtn.addEventListener('click', () => applyArrowEdits(fieldEls, ctx, state, cb));
  dom.nodeEditor.appendChild(saveBtn);
}

function applyArrowEdits(
  fieldEls: Record<string, HTMLInputElement | HTMLTextAreaElement>,
  ctx: ArrowContext,
  state: AppState,
  cb: AppCallbacks,
): void {
  if (!state.currentDef) return;
  const def = state.currentDef;

  if (ctx.isExitPhrase && fieldEls['exitPhrases']) {
    def.exit_phrases = parseExitPhrasesNewline(fieldEls['exitPhrases'].value);
    cb.refreshAll();
    cb.closeEditor();
    cb.appendLog('system', 'Exit phrases updated.');
    return;
  }

  if (fieldEls['keywords']) {
    const newSignals = parseKeywords(fieldEls['keywords'].value);
    INTENT_SIGNALS[ctx.intent] = newSignals;
    cb.appendLog('system', `Keywords for "${ctx.intent}" updated (${newSignals.length} signals).`);
  }

  if (ctx.fromStateId) {
    const s = def.states[ctx.fromStateId];
    if (s) {
      let newTarget = fieldEls['target']?.value.trim() ?? ctx.target ?? '';
      if (newTarget === 'exit' && fieldEls['exitMessage']) {
        const msg = fieldEls['exitMessage'].value.trim();
        if (msg && msg !== def.exit_message) {
          newTarget = `exit:${msg}`;
        }
      } else if (newTarget.startsWith('exit:') && fieldEls['exitMessage']) {
        const msg = fieldEls['exitMessage'].value.trim();
        newTarget = msg ? `exit:${msg}` : 'exit';
      }
      s.transitions[ctx.intent] = newTarget;
      cb.appendLog('system', `Transition ${ctx.fromStateId}/${ctx.intent} updated.`);
    }
  }

  cb.refreshAll();
  cb.closeEditor();
}
