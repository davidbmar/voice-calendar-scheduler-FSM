/**
 * debugStream.ts — WebSocket client for real-time FSM debug event streaming.
 * Connects to the backend debug endpoint and dispatches events to the UI.
 */

import { pauseSession, resumeSession, fetchDebugContext } from './api.js';

export interface DebugEvent {
  type: string;
  timestamp: number;
  session_id: string;
  state_id: string;
  data: Record<string, unknown>;
}

export interface DebugStreamController {
  close(): void;
  pause(): Promise<void>;
  resume(): Promise<void>;
  copyDebugContext(): Promise<string>;
}

const MAX_RECONNECTS = 3;
const RECONNECT_DELAY = 1000;

export function connectDebugStream(
  sessionId: string,
  onEvent: (event: DebugEvent) => void,
  onClose: () => void,
): DebugStreamController {
  let ws: WebSocket | null = null;
  let reconnectAttempts = 0;
  let closed = false;

  function connect(): void {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/fsm/sessions/${sessionId}/debug`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      reconnectAttempts = 0;
    };

    ws.onmessage = (e) => {
      try {
        const event: DebugEvent = JSON.parse(e.data);
        onEvent(event);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      if (closed) return;
      if (reconnectAttempts < MAX_RECONNECTS) {
        reconnectAttempts++;
        setTimeout(connect, RECONNECT_DELAY);
      } else {
        onClose();
      }
    };

    ws.onerror = () => {
      // onclose will fire after onerror
    };
  }

  connect();

  return {
    close() {
      closed = true;
      ws?.close();
    },

    async pause() {
      await pauseSession(sessionId);
    },

    async resume() {
      await resumeSession(sessionId);
    },

    async copyDebugContext(): Promise<string> {
      const ctx = await fetchDebugContext(sessionId);

      // Format as markdown for Claude
      const lines: string[] = [];
      lines.push('## Session Debug Context');
      lines.push(`- **Session ID:** ${ctx.session_id}`);
      lines.push(`- **Current State:** ${ctx.current_step_id}`);
      lines.push(`- **Paused:** ${ctx.is_paused ? 'yes' : 'no'}`);
      lines.push(`- **Duration:** ${ctx.duration}s`);
      lines.push('');

      // Caller state
      lines.push('### Caller State');
      const cs = ctx.caller_state;
      if (cs.phone_number) lines.push(`- phone: ${cs.phone_number}`);
      const fields = ['bedrooms', 'max_budget', 'preferred_area',
        'selected_listing_address', 'selected_time_slot',
        'caller_name', 'caller_email'];
      for (const f of fields) {
        if (cs[f]) lines.push(`- ${f}: ${cs[f]}`);
      }
      lines.push('');

      // Event timeline
      if (ctx.event_log && ctx.event_log.length > 0) {
        const events = ctx.event_log.slice(-20);
        const t0 = events[0]?.timestamp ?? 0;
        lines.push('### Event Timeline (last 20)');
        events.forEach((ev, i) => {
          const dt = (ev.timestamp - t0).toFixed(1);
          const summary = formatEventSummary(ev);
          lines.push(`${i + 1}. [${dt}s] ${ev.type.toUpperCase()} ${ev.state_id} — ${summary}`);
        });
        lines.push('');
      }

      // Recent messages
      if (ctx.recent_messages && ctx.recent_messages.length > 0) {
        lines.push('### Recent Messages (last 6)');
        for (const msg of ctx.recent_messages) {
          const content = msg.content.length > 120
            ? msg.content.slice(0, 120) + '...'
            : msg.content;
          lines.push(`- ${msg.role}: "${content}"`);
        }
      }

      const md = lines.join('\n');
      await navigator.clipboard.writeText(md);
      return md;
    },
  };
}

function formatEventSummary(ev: DebugEvent): string {
  const d = ev.data;
  switch (ev.type) {
    case 'transition':
      return `${d.from} → ${d.to} (${d.intent})`;
    case 'llm_call':
      return `"${String(d.user_text ?? '').slice(0, 60)}"`;
    case 'llm_response':
      return `"${String(d.response ?? '').slice(0, 60)}"`;
    case 'tool_exec':
      return `${d.tool_name}(${JSON.stringify(d.args ?? {}).slice(0, 60)})`;
    case 'stt':
      return `"${String(d.text ?? '').slice(0, 60)}"`;
    case 'step_complete':
      return JSON.stringify(d.extracted_data ?? {}).slice(0, 80);
    default:
      return JSON.stringify(d).slice(0, 80);
  }
}
