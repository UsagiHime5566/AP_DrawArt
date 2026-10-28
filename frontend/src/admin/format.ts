// 後臺顯示用的格式化工具與狀態標籤。

import { ApiRequestError, NetworkError } from '../shared/api';
import { serverNow } from '../shared/clock';
import type { MonitorRole } from './types';

export const ROLE_LABELS: Record<MonitorRole, string> = {
  exhibition: '展覽螢幕',
  visitor: '左・觀眾',
  ai: '中・AI',
  artist: '右・藝術家',
};

export const DISPLAY_ROLES: MonitorRole[] = ['exhibition'];

export const AI_STATUS_LABELS: Record<string, string> = {
  queued: '排隊中',
  running: '生成中',
  succeeded: '成功',
  failed: '失敗',
};

export const PLAYBACK_STATUS_LABELS: Record<string, string> = {
  waiting_for_ai: '等待 AI',
  ready: '就緒',
  preparing: '預載中',
  playing: '播放中',
  played: '已播',
};

export const RESULT_KIND_LABELS: Record<string, string> = {
  generated: 'AI 圖',
  failure_card: '失敗卡',
};

export const RUN_RESULT_LABELS: Record<string, string> = {
  completed: '完成',
  interrupted: '中斷',
};

export const SESSION_STATUS_LABELS: Record<string, string> = {
  drawing: '作畫中',
  submitted: '已送出',
  empty: '空白',
  discarded: '已捨棄',
  abandoned: '已放棄',
};

export const END_REASON_LABELS: Record<string, string> = {
  manual: '手動送出',
  timeout: '時間到',
  draft_lost: '草稿遺失',
  staff: '工作人員',
};

export const PHASE_LABELS: Record<string, string> = {
  idle: '待機',
  prepare: '預載下一件',
  commit: '切換中',
  playing: '播放中',
};

export function label(map: Record<string, string>, value: string | null | undefined): string {
  if (value === null || value === undefined) {
    return '—';
  }
  return map[value] ?? value;
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** 本地時間：今天只顯示時分秒，其他日期加上月日 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return time;
  }
  return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

/** 秒數轉成「1 分 05 秒」 */
export function formatSeconds(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} 分 ${pad(seconds % 60)} 秒`;
  }
  return `${Math.floor(minutes / 60)} 小時 ${pad(minutes % 60)} 分`;
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) {
    return '—';
  }
  if (ms < 10_000) {
    return `${(ms / 1000).toFixed(1)} 秒`;
  }
  return formatSeconds(ms / 1000);
}

/** 依主機時間計算「幾秒前」 */
export function formatAgo(iso: string | null | undefined): string {
  if (!iso) {
    return '無紀錄';
  }
  const ms = serverNow() - Date.parse(iso);
  if (Number.isNaN(ms)) {
    return iso;
  }
  if (ms < 1500) {
    return '剛剛';
  }
  return `${formatSeconds(ms / 1000)}前`;
}

/** 依主機時間計算經過秒數 */
export function secondsSince(iso: string | null | undefined): number | null {
  if (!iso) {
    return null;
  }
  const ms = serverNow() - Date.parse(iso);
  return Number.isNaN(ms) ? null : Math.max(0, ms / 1000);
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) {
    return '—';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit >= 3 ? 1 : 0)} ${units[unit]}`;
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : '—';
}

export function describeError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return `${error.message}（${error.code}）`;
  }
  if (error instanceof NetworkError) {
    return '無法連線到主機，請確認網路後再試一次。';
  }
  return error instanceof Error ? error.message : String(error);
}
