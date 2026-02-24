/**
 * extractedDataStore.ts — IndexedDB persistence for extracted session data.
 *
 * Stores checkpoint data (field_progress, step_complete) so it survives
 * overlay close/reopen and page reloads. Auto-purges records older than 2 hours.
 */

const DB_NAME = 'scheduler-extracted-data';
const DB_VERSION = 1;
const STORE_NAME = 'checkpoints';
const TTL_MS = 2 * 60 * 60 * 1000; // 2 hours

interface CheckpointRecord {
  id: string;          // sessionId:stateId:key
  sessionId: string;
  stateId: string;
  key: string;
  value: unknown;
  timestamp: number;
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'id' });
        store.createIndex('sessionId', 'sessionId', { unique: false });
        store.createIndex('timestamp', 'timestamp', { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Persist extracted fields for a session + state. */
export async function saveCheckpoint(
  sessionId: string,
  stateId: string,
  data: Record<string, unknown>,
): Promise<void> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);
  const now = Date.now();

  for (const [key, value] of Object.entries(data)) {
    if (key === 'intent' || key === 'done') continue;
    if (value === null || value === undefined) continue;
    const record: CheckpointRecord = {
      id: `${sessionId}:${stateId}:${key}`,
      sessionId,
      stateId,
      key,
      value,
      timestamp: now,
    };
    store.put(record);
  }

  db.close();
}

/** Load all checkpoint data for a session, grouped by stateId. */
export async function loadSession(
  sessionId: string,
): Promise<Map<string, Record<string, unknown>>> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readonly');
  const store = tx.objectStore(STORE_NAME);
  const index = store.index('sessionId');

  return new Promise((resolve) => {
    const result = new Map<string, Record<string, unknown>>();
    const req = index.openCursor(IDBKeyRange.only(sessionId));

    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) {
        const rec = cursor.value as CheckpointRecord;
        if (!result.has(rec.stateId)) {
          result.set(rec.stateId, {});
        }
        result.get(rec.stateId)![rec.key] = rec.value;
        cursor.continue();
      } else {
        db.close();
        resolve(result);
      }
    };

    req.onerror = () => {
      db.close();
      resolve(result);
    };
  });
}

/** Delete records older than 2 hours. Returns count of purged records. */
export async function purgeExpired(): Promise<number> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);
  const index = store.index('timestamp');
  const cutoff = Date.now() - TTL_MS;
  let count = 0;

  return new Promise((resolve) => {
    const req = index.openCursor(IDBKeyRange.upperBound(cutoff));
    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) {
        cursor.delete();
        count++;
        cursor.continue();
      } else {
        db.close();
        resolve(count);
      }
    };
    req.onerror = () => {
      db.close();
      resolve(count);
    };
  });
}
