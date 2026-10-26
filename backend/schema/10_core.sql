-- 核心資料表（共用，由整合者維護；各模組不得自行修改本檔）
-- 時間欄位一律為 UTC ISO 8601 字串，例如 2026-10-27T03:05:00.123Z
-- 路徑欄位一律為相對於 data_dir 的正斜線路徑，例如 submissions/<id>/visitor.png

CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS artworks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL
        CHECK (status IN ('drawing', 'submitted', 'empty', 'discarded', 'abandoned')),
    artwork_id INTEGER NOT NULL REFERENCES artworks(id),
    settings_version_id INTEGER NOT NULL REFERENCES settings_versions(id),
    tablet_id TEXT,
    started_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    next_session_available_at TEXT,
    submission_key TEXT,
    submission_sha256 TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
-- 同時最多只有一個作畫中的場次
CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_one_drawing
    ON sessions(status) WHERE status = 'drawing';

CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id),
    receipt_order INTEGER NOT NULL UNIQUE,
    submission_key TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    dir_path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(id),
    receipt_order INTEGER NOT NULL UNIQUE,
    settings_version_id INTEGER NOT NULL REFERENCES settings_versions(id),
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    current_attempt_id TEXT,
    lease_owner TEXT,
    lease_heartbeat_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_jobs_status_order ON ai_jobs(status, receipt_order);

CREATE TABLE IF NOT EXISTS ai_attempts (
    id TEXT PRIMARY KEY,
    ai_job_id INTEGER NOT NULL REFERENCES ai_jobs(id),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    model TEXT NOT NULL,
    quality TEXT NOT NULL,
    size TEXT NOT NULL,
    prompt TEXT NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    dir_path TEXT NOT NULL,
    result_file TEXT,
    request_id TEXT,
    usage_json TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ai_attempts_job ON ai_attempts(ai_job_id);

CREATE TABLE IF NOT EXISTS playback_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(id),
    receipt_order INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL
        CHECK (status IN ('waiting_for_ai', 'ready', 'preparing', 'playing', 'played')),
    result_kind TEXT CHECK (result_kind IN ('generated', 'failure_card')),
    ai_attempt_id TEXT,
    failure_text TEXT,
    content_version INTEGER NOT NULL DEFAULT 0,
    min_display_seconds INTEGER,
    played_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playback_items_status_order
    ON playback_items(status, receipt_order);
