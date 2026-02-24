/**
 * intentClassifier.ts — Keyword-based intent classifier for scheduling workflows.
 * Extended with scheduling-specific intents.
 */

export interface ClassifyResult {
  intent: string;
  score: number;
}

// ── Keyword lists ──────────────────────────────────────────────────

export const INTENT_SIGNALS: Record<string, string[]> = {
  // Scheduling intents
  gathered: ['search', 'find', 'look for', 'searching'],
  selected: ['that one', 'pick', 'choose', 'select', 'first one', 'second one'],
  search_again: ['search again', 'different', 'other options', 'something else'],
  time_selected: ['that time', 'works for me', 'sounds good', 'book it'],
  confirmed: ['confirm', 'yes', 'correct', 'that is right', 'looks good'],
  no_times: ['none of those', 'no times', 'doesn\'t work', 'don\'t work'],
  retry: ['try again', 'retry', 'one more time'],
  done: ['thank you', 'thanks', 'bye', 'goodbye'],
  // General intents
  confirm: [
    'yes', 'go ahead', 'do it', 'confirmed', 'approved', 'proceed',
    'affirmative', 'sure', 'ok', 'green light', 'go for it',
  ],
  deny: [
    'no', "don't", 'cancel', 'forget it', 'never mind', 'stop that',
    'negative', 'abort that', 'scratch that', 'nope',
  ],
  cancel: [
    'cancel', 'never mind', 'forget it', 'stop', 'end call', 'hang up',
  ],
};

// ── Helpers ──────────────────────────────────────────────────────────

function countMatches(input: string, signals: string[]): number {
  let count = 0;
  for (const sig of signals) {
    if (sig.length <= 3) {
      const re = new RegExp(`\\b${sig.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
      if (re.test(input)) count++;
    } else {
      if (input.includes(sig)) count++;
    }
  }
  return count;
}

// ── Main classifier ─────────────────────────────────────────────────

export function classifyIntent(text: string): ClassifyResult {
  const lower = text.toLowerCase().trim();

  let bestIntent = 'unknown';
  let maxScore = 0;

  for (const [intent, signals] of Object.entries(INTENT_SIGNALS)) {
    const score = countMatches(lower, signals);
    if (score > maxScore) {
      maxScore = score;
      bestIntent = intent;
    }
  }

  return { intent: bestIntent, score: maxScore };
}
