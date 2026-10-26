// 展示角色：取自網址最後一段（/display/ai），開發時也可用 ?role=ai。

import type { DisplayRole, DisplaySnapshot } from '../shared/types';

const ROLES: readonly DisplayRole[] = ['visitor', 'ai', 'artist'];

function isRole(value: string | null | undefined): value is DisplayRole {
  return value !== null && value !== undefined && (ROLES as readonly string[]).includes(value);
}

export function resolveRole(): DisplayRole | null {
  const fromQuery = new URLSearchParams(window.location.search).get('role');
  if (isRole(fromQuery)) {
    return fromQuery;
  }
  const segment = window.location.pathname.split('/').filter(Boolean).pop();
  return isRole(segment) ? segment : null;
}

/** 此角色需要預載的圖片網址；沒有圖片（待機、失敗卡）時為 null */
export function imageUrlForRole(snapshot: DisplaySnapshot, role: DisplayRole): string | null {
  switch (role) {
    case 'visitor':
      return snapshot.kind === 'item' ? (snapshot.visitor?.url ?? null) : null;
    case 'ai':
      return snapshot.kind === 'item' && snapshot.ai.type === 'image' ? snapshot.ai.url : null;
    case 'artist':
      return snapshot.artist?.url ?? null;
  }
}

export function makeClientId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '');
  }
  return Array.from({ length: 4 }, () => Math.random().toString(16).slice(2, 10)).join('');
}
