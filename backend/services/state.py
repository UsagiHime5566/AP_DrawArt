"""runtime_state 鍵值存取（JSON 值）。

已定義的鍵：
- intake_enabled: bool                         接待開關
- intake_paused_for_stop: bool                 接待是「結束展覽」時為收尾而暫停，下次啟動自動恢復
- current_settings_version_id: int            目前設定版本
- active_artwork_id: int | None                目前啟用原圖
- base_image_stat: {"mtime_ns", "size"} | None  上次檢查 base.webp 的檔案狀態
- worker_heartbeat: {"pid", "at", "job_id"}    AI Worker 心跳
- launcher_requests: {"shutdown": bool, "relaunch_displays": bool}
                                               後臺對啟動器的要求，啟動器處理後清除
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.timeutil import now_iso

_DEFAULTS: dict[str, Any] = {
    "intake_enabled": True,
    "active_artwork_id": None,
    "base_image_stat": None,
    "worker_heartbeat": None,
    "launcher_requests": {"shutdown": False, "relaunch_displays": False},
}


def get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value_json FROM runtime_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value_json"])


def set(conn: sqlite3.Connection, key: str, value: Any) -> None:  # noqa: A001
    conn.execute(
        """
        INSERT INTO runtime_state (key, value_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                                       updated_at = excluded.updated_at
        """,
        (key, json.dumps(value, ensure_ascii=False), now_iso()),
    )


def ensure_defaults(conn: sqlite3.Connection) -> None:
    for key, value in _DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO runtime_state (key, value_json, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )


def is_intake_enabled(conn: sqlite3.Connection) -> bool:
    return bool(get(conn, "intake_enabled", True))


def set_intake_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    """工作人員明確切換接待；之後不再由啟動流程自動恢復。"""
    set(conn, "intake_enabled", bool(enabled))
    set(conn, "intake_paused_for_stop", False)


def pause_intake_for_stop(conn: sqlite3.Connection) -> None:
    """結束展覽：暫停接待以免收尾期間開新場；原本就暫停的維持工作人員的選擇。"""
    if is_intake_enabled(conn):
        set(conn, "intake_enabled", False)
        set(conn, "intake_paused_for_stop", True)


def resume_intake_after_stop(conn: sqlite3.Connection) -> bool:
    """啟動時恢復上次結束展覽所暫停的接待；回傳是否有恢復。"""
    if not get(conn, "intake_paused_for_stop", False):
        return False
    set_intake_enabled(conn, True)
    return True
