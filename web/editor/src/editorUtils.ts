/**
 * Pure parsing/serialization helpers used by the node editor.
 * Extracted so the regex logic is testable without DOM dependencies.
 */

/** Parse transition lines as displayed in the node editor ("intent → target" per line). */
export function parseTransitions(text: string): Record<string, string> {
  const trans: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const match = trimmed.match(/^(\S+)\s*(?:→|->)\s*(.+)$/);
    if (match) {
      trans[match[1]] = match[2].trimEnd();
    }
  }
  return trans;
}

/** Serialize transitions for display in the node editor. */
export function serializeTransitions(transitions: Record<string, string>): string {
  return Object.entries(transitions).map(([k, v]) => `${k} → ${v}`).join('\n');
}

/** Parse comma-separated exit phrases. */
export function parseExitPhrases(text: string): string[] {
  return text.split(',').map(s => s.trim()).filter(Boolean);
}

/** Parse newline-separated exit phrases. */
export function parseExitPhrasesNewline(text: string): string[] {
  return text.split('\n').map(s => s.trim()).filter(Boolean);
}

/** Parse newline-separated keywords. */
export function parseKeywords(text: string): string[] {
  return text.split('\n').map(s => s.trim().toLowerCase()).filter(Boolean);
}
