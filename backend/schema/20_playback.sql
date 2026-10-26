-- 播放控制器專用資料表（由 playback 模組維護，可依實作調整欄位）

CREATE TABLE IF NOT EXISTS display_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    display_revision INTEGER NOT NULL DEFAULT 0,
    phase TEXT NOT NULL DEFAULT 'idle',
    current_item_id INTEGER REFERENCES playback_items(id),
    current_revision INTEGER,
    current_run_id INTEGER,
    pending_item_id INTEGER REFERENCES playback_items(id),
    pending_revision INTEGER,
    switch_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playback_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playback_item_id INTEGER NOT NULL REFERENCES playback_items(id),
    display_revision INTEGER NOT NULL,
    min_display_seconds INTEGER NOT NULL,
    committed_at TEXT,
    switch_at TEXT,
    visitor_presented_at TEXT,
    ai_presented_at TEXT,
    artist_presented_at TEXT,
    timing_started_at TEXT,
    ended_at TEXT,
    result TEXT CHECK (result IN ('completed', 'interrupted')),
    note TEXT
);

CREATE TABLE IF NOT EXISTS display_clients (
    role TEXT PRIMARY KEY CHECK (role IN ('visitor', 'ai', 'artist')),
    client_id TEXT,
    connected INTEGER NOT NULL DEFAULT 0,
    shown_revision INTEGER,
    ready_revision INTEGER,
    last_heartbeat_at TEXT,
    remote_addr TEXT,
    updated_at TEXT NOT NULL
);
