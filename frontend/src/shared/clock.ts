// 主機時間校正：倒數、換場與同步切換都以主機時間為準。

let offsetMs = 0;
let bestRoundTripMs = Number.POSITIVE_INFINITY;
let lastSyncAt = 0;

/**
 * 以回應中的 server_now 校正時差。requestStartMs 為送出請求時的 Date.now()；
 * 只採用往返時間較短（較準）的樣本，舊樣本 60 秒後失效。
 */
export function syncServerClock(serverNowIso: string, requestStartMs?: number): void {
  const now = Date.now();
  const roundTrip = requestStartMs === undefined ? 0 : Math.max(0, now - requestStartMs);
  const stale = now - lastSyncAt > 60_000;
  if (!stale && roundTrip > bestRoundTripMs) {
    return;
  }
  const serverMs = Date.parse(serverNowIso);
  if (Number.isNaN(serverMs)) {
    return;
  }
  offsetMs = serverMs + roundTrip / 2 - now;
  bestRoundTripMs = roundTrip;
  lastSyncAt = now;
}

/** 目前主機時間（epoch 毫秒） */
export function serverNow(): number {
  return Date.now() + offsetMs;
}

/** 距離某個主機時間點還剩幾毫秒（可為負數） */
export function msUntil(iso: string): number {
  return Date.parse(iso) - serverNow();
}
