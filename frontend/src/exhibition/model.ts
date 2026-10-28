import type { ArtworkInfo, Progress } from '../shared/types';

export interface ExhibitionState {
  revision: number;
  mode: 'gallery' | 'drawing' | 'personal';
  stage: 'visitor' | 'ai' | 'artist';
  kind: 'personal' | 'premiere' | 'history' | null;
  can_start: boolean;
  locked: boolean;
  connected: boolean;
  session_id: string | null;
  submission_id: string | null;
  playback_item_id: number | null;
  receipt_order: number | null;
  image_url: string | null;
  failure_text: string | null;
  artwork: ArtworkInfo | null;
  started_at: string | null;
  seconds: number;
  /** 換段淡入秒數（後臺設定，即時生效）；0 為直接切換 */
  fade_seconds: number;
  waiting_for_ai: boolean;
  progress: Progress;
}

/** 長按的 repeat 不算；只計算目前五秒滑動視窗內的獨立按鍵。 */
export function escapeSequence() {
  let presses: number[] = [];
  return (now: number, repeat = false): boolean => {
    if (repeat) return false;
    presses = presses.filter((time) => now - time <= 5000);
    presses.push(now);
    if (presses.length < 5) return false;
    presses = [];
    return true;
  };
}

export function isTipContact(event: Pick<PointerEvent, 'pointerType' | 'button'>): boolean {
  // 滑鼠左鍵供開發與將筆尖映射成滑鼠的驅動使用；移動事件不進此入口。
  return (event.pointerType === 'pen' || event.pointerType === 'mouse') && event.button === 0;
}
