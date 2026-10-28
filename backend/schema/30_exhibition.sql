-- v2 新增分段播放紀錄；舊作品、三螢幕紀錄與待播項保持原樣。
CREATE TABLE IF NOT EXISTS exhibition_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playback_item_id INTEGER NOT NULL REFERENCES playback_items(id),
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'premiere', 'history')),
    stage TEXT NOT NULL CHECK (stage IN ('visitor', 'ai', 'artist')),
    revision INTEGER NOT NULL,
    seconds INTEGER NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    result TEXT CHECK (result IN ('completed', 'interrupted'))
);
