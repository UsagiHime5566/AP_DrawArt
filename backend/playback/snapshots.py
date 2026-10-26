"""播放快照、待機畫面與生成進度的組裝（同步、唯讀）。"""

from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from backend.config import FAILURE_TEXT
from backend.services import state

DEFAULT_ESTIMATE_SECONDS = 60
ESTIMATE_SAMPLE_SIZE = 10

_ITEM_SQL = """
SELECT p.id, p.receipt_order, p.status, p.result_kind, p.failure_text, p.content_version,
       p.submission_id, s.dir_path,
       a.id AS artwork_id, a.file_path AS artwork_path,
       a.width AS artwork_width, a.height AS artwork_height,
       t.result_file
FROM playback_items p
JOIN submissions s ON s.id = p.submission_id
JOIN sessions se ON se.id = s.session_id
JOIN artworks a ON a.id = se.artwork_id
LEFT JOIN ai_attempts t ON t.id = p.ai_attempt_id
WHERE p.id = ?
"""


def media_url(relative: str, version: int | None = None) -> str:
    url = "/media/" + relative.lstrip("/")
    return url if version is None else f"{url}?v={version}"


def _artwork(artwork_id: int, file_path: str, width: int, height: int) -> dict[str, Any]:
    return {
        "id": artwork_id,
        "url": media_url(file_path, artwork_id),
        "width": width,
        "height": height,
    }


def active_artwork(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """目前啟用的原圖；尚未登錄時退回最新一張。"""
    row = None
    artwork_id = state.get(conn, "active_artwork_id")
    if artwork_id is not None:
        row = conn.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM artworks ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return _artwork(row["id"], row["file_path"], row["width"], row["height"])


def item_snapshot(conn: sqlite3.Connection, item_id: int, revision: int) -> dict[str, Any] | None:
    row = conn.execute(_ITEM_SQL, (item_id,)).fetchone()
    if row is None:
        return None
    version = row["content_version"]
    if row["result_kind"] == "generated" and row["result_file"]:
        ai: dict[str, Any] = {"type": "image", "url": media_url(row["result_file"], version)}
    else:
        ai = {"type": "failure", "text": row["failure_text"] or FAILURE_TEXT}
    return {
        "revision": revision,
        "kind": "item",
        "playback_item_id": row["id"],
        "receipt_order": row["receipt_order"],
        "visitor": {"url": media_url(f"{row['dir_path']}/visitor.png", version)},
        "ai": ai,
        "artist": _artwork(
            row["artwork_id"], row["artwork_path"], row["artwork_width"], row["artwork_height"]
        ),
    }


def standby_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """尚無任何作品時的待機畫面，版本固定為 0。"""
    return {
        "revision": 0,
        "kind": "standby",
        "playback_item_id": None,
        "receipt_order": None,
        "visitor": None,
        "ai": {"type": "standby"},
        "artist": active_artwork(conn),
    }


def progress(conn: sqlite3.Connection) -> dict[str, Any]:
    """中螢幕生成進度：執行中的工作、估計耗時與排隊件數。"""
    running = conn.execute(
        """
        SELECT j.started_at, t.started_at AS attempt_started_at
        FROM ai_jobs j
        LEFT JOIN ai_attempts t ON t.id = j.current_attempt_id
        WHERE j.status = 'running'
        ORDER BY j.receipt_order
        LIMIT 1
        """
    ).fetchone()
    queued = conn.execute("SELECT COUNT(*) FROM ai_jobs WHERE status = 'queued'").fetchone()[0]
    durations = [
        row[0]
        for row in conn.execute(
            """
            SELECT duration_ms FROM ai_attempts
            WHERE status = 'succeeded' AND duration_ms IS NOT NULL
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (ESTIMATE_SAMPLE_SIZE,),
        )
    ]
    estimate = (
        max(1, round(statistics.median(durations) / 1000)) if durations else DEFAULT_ESTIMATE_SECONDS
    )
    started_at = None
    if running is not None:
        started_at = running["started_at"] or running["attempt_started_at"]
    return {
        "active": running is not None,
        "started_at": started_at,
        "estimate_seconds": estimate,
        "queued_after": int(queued),
    }
