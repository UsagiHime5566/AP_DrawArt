// 以場次為單位，把草稿（向量筆畫、提交識別碼與凍結快照）保存在 IndexedDB。
// 草稿只協助頁面意外重載後恢復；主機確認收件前，它不代表作品已保存。

import type { Draft } from './model';

const DB_NAME = 'ap-drawart-draw';
const DB_VERSION = 1;
const STORE = 'drafts';

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (!dbPromise) {
    const opening = new Promise<IDBDatabase>((resolve, reject) => {
      if (typeof indexedDB === 'undefined') {
        reject(new Error('此瀏覽器不支援 IndexedDB'));
        return;
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) {
          request.result.createObjectStore(STORE, { keyPath: 'sessionId' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error('無法開啟草稿資料庫'));
    });
    // 開啟失敗時下次重試
    opening.catch(() => {
      dbPromise = null;
    });
    dbPromise = opening;
  }
  return dbPromise;
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = action(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request.result);
    transaction.onerror = () => reject(transaction.error ?? new Error('草稿存取失敗'));
    transaction.onabort = () => reject(transaction.error ?? new Error('草稿存取中止'));
  });
}

export async function getDraft(sessionId: string): Promise<Draft | null> {
  const result = await run<Draft | undefined>('readonly', (store) => store.get(sessionId) as IDBRequest<Draft | undefined>);
  return result ?? null;
}

export async function putDraft(draft: Draft): Promise<void> {
  await run('readwrite', (store) => store.put(draft));
}

export async function deleteDraft(sessionId: string): Promise<void> {
  await run('readwrite', (store) => store.delete(sessionId));
}

/** 刪除其他場次的草稿，避免下一位看到前一位的畫作 */
export async function deleteDraftsExcept(keepSessionId: string | null): Promise<void> {
  const keys = await run<IDBValidKey[]>('readonly', (store) => store.getAllKeys());
  for (const key of keys) {
    if (key !== keepSessionId) {
      await run('readwrite', (store) => store.delete(key));
    }
  }
}

/** 草稿失敗不阻擋作畫（例如私密瀏覽），只記錄到主控台 */
export function logDraftError(error: unknown): void {
  console.warn('草稿存取失敗', error);
}
