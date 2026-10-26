// 後臺 API 型別，對應 docs/implementation-contract.md 第 6.4、6.5 節。

import type { DisplayRole, IsoTime, Progress, SessionView } from '../shared/types';

export interface DisplayClientInfo {
  connected: boolean;
  shown_revision: number | null;
  last_heartbeat_at: IsoTime | null;
}

export type HealthStatus = 'ok' | 'degraded' | 'setup_required';

export interface HealthChecks {
  database: { ok: boolean };
  frontend: { ok: boolean; missing: string[] };
  artwork: { ok: boolean; artwork_id: number | null };
  worker: { ok: boolean; pid: number | null; last_heartbeat_at: IsoTime | null };
  displays: { ok: boolean; roles: Partial<Record<DisplayRole, DisplayClientInfo>> };
  disk: {
    ok: boolean;
    free_bytes: number;
    total_bytes: number;
    free_ratio: number;
    discarding: boolean;
  };
  log: { ok: boolean; mode: string; last_error: string | null };
  openai_key: { ok: boolean; source: string | null };
}

export interface LauncherRequests {
  shutdown: boolean;
  relaunch_displays: boolean;
}

export interface HealthBody {
  server_now?: IsoTime;
  status: HealthStatus;
  admin_urls: string[];
  launcher_requests: LauncherRequests;
  checks: Partial<HealthChecks>;
}

export interface PlaybackCurrent {
  playback_item_id: number;
  receipt_order: number;
  submission_id: string;
  result_kind: 'generated' | 'failure_card' | null;
  timing_started_at: IsoTime | null;
  min_display_seconds: number | null;
  played: boolean;
}

export interface PlaybackPending {
  playback_item_id: number;
  receipt_order: number;
  ready_roles: DisplayRole[];
}

export interface PlaybackStatus {
  phase: string;
  display_revision: number;
  current: PlaybackCurrent | null;
  pending: PlaybackPending | null;
  clients: Partial<Record<DisplayRole, DisplayClientInfo>>;
}

export interface OverviewCounts {
  ai_queued: number;
  ai_running: number;
  playback_unplayed: number;
  played_total: number;
  submissions_total: number;
  empty_total: number;
  discarded_total: number;
  abandoned_total: number;
}

export interface OverviewResponse {
  server_now: IsoTime;
  intake_enabled: boolean;
  health: HealthBody;
  active_session: SessionView | null;
  next_session_available_at: IsoTime | null;
  counts: OverviewCounts;
  playback: PlaybackStatus;
  progress: Progress;
  settings_version_id: number;
}

export type AiJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export interface AiJobRow {
  id: number;
  submission_id: string;
  receipt_order: number;
  status: AiJobStatus;
  started_at: IsoTime | null;
  finished_at: IsoTime | null;
  duration_ms: number | null;
  model: string | null;
  error_code: string | null;
  error_message: string | null;
}

export type PlaybackItemStatus = 'waiting_for_ai' | 'ready' | 'preparing' | 'playing' | 'played';

export interface PlaybackItemRow {
  id: number;
  submission_id: string;
  receipt_order: number;
  status: PlaybackItemStatus;
  result_kind: 'generated' | 'failure_card' | null;
  played_at: IsoTime | null;
  visitor_url: string | null;
  ai_url: string | null;
}

export interface PlaybackRunRow {
  id: number;
  playback_item_id: number;
  receipt_order: number | null;
  timing_started_at: IsoTime | null;
  ended_at: IsoTime | null;
  result: 'completed' | 'interrupted' | null;
  min_display_seconds: number | null;
}

export interface SessionRow {
  id: string;
  status: SessionView['status'];
  started_at: IsoTime;
  ended_at: IsoTime | null;
  end_reason: string | null;
}

export interface QueuesResponse {
  server_now: IsoTime;
  ai_jobs: AiJobRow[];
  playback_items: PlaybackItemRow[];
  playback_runs: PlaybackRunRow[];
  sessions: SessionRow[];
}

export interface AdminSettings {
  drawing_duration_seconds: number;
  turnaround_seconds: number;
  display_min_seconds: number;
  reference_opacity: number;
  ai_model: string;
  ai_prompt: string;
  ai_quality: string;
  ai_size: string;
  ai_timeout_seconds: number;
}

export interface SettingsResponse {
  server_now: IsoTime;
  version_id: number;
  settings: AdminSettings;
  defaults: AdminSettings;
  allowed_models: string[];
  allowed_qualities: string[];
}

export interface LogsResponse {
  server_now: IsoTime;
  offset: number;
  next_offset: number;
  size: number;
  text: string;
}

export interface MonitorInfo {
  index: number;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  primary: boolean;
}

export interface DisplaysConfig {
  visitor: { monitor: number };
  ai: { monitor: number };
  artist: { monitor: number };
  browser: string;
}

export interface DisplaysResponse {
  server_now: IsoTime;
  config: DisplaysConfig;
  monitors: MonitorInfo[];
  clients: Partial<Record<DisplayRole, DisplayClientInfo>>;
}

export interface OpenAiKeyStatus {
  configured: boolean;
  source: 'env' | 'file' | null;
}

export interface AiTestResult {
  id: string | null;
  status: 'succeeded' | 'failed';
  model: string | null;
  quality: string | null;
  size: string | null;
  duration_ms: number | null;
  result_url: string | null;
  error_code: string | null;
  error_message: string | null;
}
