// 平板繪圖的資料模型：筆畫以向量保存，草稿與匯出都由同一份操作紀錄重播。

export const CANVAS_WIDTH = 1920;
export const CANVAS_HEIGHT = 1080;

export type Tool = 'pen' | 'eraser';

export interface Stroke {
  id: string;
  tool: Tool;
  color: string;
  /** 邏輯像素的筆刷粗細 */
  size: number;
  /** 邏輯座標 [x0, y0, x1, y1, ...] */
  points: number[];
}

/** 復原／重做的單位：一筆完整筆畫，或一次清空 */
export type Op = { kind: 'stroke'; stroke: Stroke } | { kind: 'clear' };

export type FreezeReason = 'manual' | 'timeout';

/** 凍結後的送出快照；重送時一律使用同一份 PNG，確保內容雜湊一致 */
export interface FrozenSnapshot {
  reason: FreezeReason;
  png: ArrayBuffer;
}

export interface Draft {
  sessionId: string;
  submissionKey: string;
  ops: Op[];
  frozen: FrozenSnapshot | null;
  updatedAt: number;
}

/** 32 字元亂數識別碼；純 HTTP 區網不是安全環境，不能用 crypto.randomUUID */
export function randomId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}
