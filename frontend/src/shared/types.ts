// 平板與展示頁共用的 API 型別，對應 docs/implementation-contract.md。

/** UTC ISO 8601，例如 2026-10-27T03:05:00.123Z */
export type IsoTime = string;

export interface ArtworkInfo {
  id: number;
  url: string;
  width: number;
  height: number;
}

export interface PublicSettings {
  drawing_duration_seconds: number;
  turnaround_seconds: number;
  reference_opacity: number;
}

export interface ConfigResponse {
  server_now: IsoTime;
  artwork: ArtworkInfo;
  settings: PublicSettings;
  canvas: { width: number; height: number };
  intake_enabled: boolean;
}

export type SessionStatus = 'drawing' | 'submitted' | 'empty' | 'discarded' | 'abandoned';

export interface SessionView {
  id: string;
  status: SessionStatus;
  artwork: ArtworkInfo;
  settings: PublicSettings;
  started_at: IsoTime;
  deadline_at: IsoTime;
  ended_at: IsoTime | null;
  next_session_available_at: IsoTime | null;
  submission: { id: string; queue_ahead: number } | null;
}

export interface CurrentSessionResponse {
  server_now: IsoTime;
  intake_enabled: boolean;
  next_session_available_at: IsoTime | null;
  active_session: SessionView | null;
}

export interface SessionResponse {
  server_now: IsoTime;
  session: SessionView;
}

export type SubmitResult = 'submitted' | 'empty' | 'discarded';

export interface SubmitResponse {
  server_now: IsoTime;
  result: SubmitResult;
  session: SessionView;
}

export type DisplayRole = 'visitor' | 'ai' | 'artist';

export type AiPanel =
  | { type: 'image'; url: string }
  | { type: 'failure'; text: string }
  | { type: 'standby' };

export interface DisplaySnapshot {
  revision: number;
  kind: 'item' | 'standby';
  playback_item_id: number | null;
  receipt_order: number | null;
  visitor: { url: string } | null;
  ai: AiPanel;
  artist: ArtworkInfo;
}

export interface Progress {
  active: boolean;
  started_at: IsoTime | null;
  estimate_seconds: number;
  queued_after: number;
}

export interface DisplayCurrentResponse {
  server_now: IsoTime;
  current: DisplaySnapshot | null;
  pending: DisplaySnapshot | null;
  progress: Progress;
}

/** 主機送往展示頁的 WebSocket 訊息 */
export type ServerMessage =
  | {
      type: 'hello';
      server_now: IsoTime;
      role: DisplayRole;
      current: DisplaySnapshot | null;
      pending: DisplaySnapshot | null;
      progress: Progress;
    }
  | { type: 'prepare'; server_now: IsoTime; snapshot: DisplaySnapshot }
  | { type: 'commit'; server_now: IsoTime; revision: number; switch_at: IsoTime }
  | { type: 'progress'; server_now: IsoTime; progress: Progress };

/** 展示頁送往主機的 WebSocket 訊息 */
export type ClientMessage =
  | { type: 'ready'; revision: number }
  | { type: 'presented'; revision: number }
  | { type: 'heartbeat'; shown_revision: number | null }
  | { type: 'client_error'; message: string };

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> };
}
