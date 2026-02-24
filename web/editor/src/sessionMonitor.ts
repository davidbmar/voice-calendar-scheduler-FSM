/**
 * sessionMonitor.ts — Right panel that polls active sessions and highlights
 * the current state in the workflow graph.
 */

import { fetchSessions, type SessionSummary } from './api.js';
import type { AppCallbacks } from './appContext.js';

let pollInterval: ReturnType<typeof setInterval> | null = null;

export function startSessionMonitor(
  container: HTMLElement,
  onActiveState: (stateId: string | null) => void,
  cb?: AppCallbacks,
): void {
  if (pollInterval) clearInterval(pollInterval);

  const update = async () => {
    try {
      const data = await fetchSessions();
      renderSessions(container, data.sessions, cb);

      // Highlight the active state of the first non-done session
      const active = data.sessions.find(s => !s.is_done);
      onActiveState(active ? active.current_step_id : null);
    } catch {
      // API not available yet — ignore
    }
  };

  update();
  pollInterval = setInterval(update, 2000);
}

export function stopSessionMonitor(): void {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

function renderSessions(
  container: HTMLElement,
  sessions: SessionSummary[],
  cb?: AppCallbacks,
): void {
  container.textContent = '';

  if (sessions.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'session-empty';
    empty.textContent = 'No active sessions';
    container.appendChild(empty);
    return;
  }

  for (const session of sessions) {
    const card = document.createElement('div');
    card.className = `session-card ${session.is_done ? 'done' : 'active'}`;

    const header = document.createElement('div');
    header.className = 'session-header';
    header.textContent = session.session_id;

    const state = document.createElement('div');
    state.className = 'session-state';
    state.textContent = `State: ${session.current_step_id}`;

    const status = document.createElement('div');
    status.className = `session-status ${session.is_done ? 'done' : 'active'}`;
    status.textContent = session.is_done ? 'DONE' : 'ACTIVE';

    card.appendChild(header);
    card.appendChild(state);
    card.appendChild(status);

    const phone = session.caller_state?.phone_number;
    if (phone) {
      const phoneEl = document.createElement('div');
      phoneEl.className = 'session-phone';
      phoneEl.textContent = String(phone);
      card.appendChild(phoneEl);
    }

    // Debug button for active sessions
    if (!session.is_done && cb) {
      const debugBtn = document.createElement('button');
      debugBtn.className = 'debug-btn';
      debugBtn.textContent = 'DEBUG';
      debugBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        cb.openLiveDebug(session.session_id);
      });
      card.appendChild(debugBtn);
    }

    container.appendChild(card);
  }
}
