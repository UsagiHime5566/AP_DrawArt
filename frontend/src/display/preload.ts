// 圖片預載與解碼：切換前就完成解碼，切換瞬間不出現空白或逐行載入。

const MAX_ATTEMPTS = 6;
const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 5_000];
const KEEP_DECODED = 12;

const pending = new Map<string, Promise<boolean>>();
// 保留已解碼的 Image 物件，避免瀏覽器把解碼結果釋放
const decoded = new Map<string, HTMLImageElement>();

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function remember(url: string, image: HTMLImageElement): void {
  decoded.delete(url);
  decoded.set(url, image);
  while (decoded.size > KEEP_DECODED) {
    const oldest = decoded.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    decoded.delete(oldest);
  }
}

async function loadWithRetry(url: string, onError: (message: string) => void): Promise<boolean> {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    const image = new Image();
    image.decoding = 'async';
    image.src = url;
    try {
      await image.decode();
      remember(url, image);
      return true;
    } catch (error) {
      onError(`圖片載入失敗（第 ${attempt} 次）：${url} ${error instanceof Error ? error.message : ''}`.trim());
      if (attempt < MAX_ATTEMPTS) {
        await sleep(RETRY_DELAYS_MS[attempt - 1] ?? 5_000);
      }
    }
  }
  return false;
}

/**
 * 載入並解碼圖片；重試數次仍失敗時回傳 false（呼叫端以黑畫面代替，避免播放停住）。
 * 成功的結果會快取，失敗的不快取以便之後重試。
 */
export function preloadImage(url: string, onError: (message: string) => void): Promise<boolean> {
  if (decoded.has(url)) {
    return Promise.resolve(true);
  }
  const existing = pending.get(url);
  if (existing) {
    return existing;
  }
  const task = loadWithRetry(url, onError).finally(() => pending.delete(url));
  pending.set(url, task);
  return task;
}
