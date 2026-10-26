"""播放與生成進度的唯讀查詢（後臺與健康檢查使用）。"""

from __future__ import annotations

import sqlite3
from typing import Any

from backend.config import DISPLAY_ROLES
from backend.playback import engine, snapshots


def _clients(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = {
        row["role"]: row
        for row in conn.execute(
            "SELECT role, connected, shown_revision, ready_revision, last_heartbeat_at FROM display_clients"
        )
    }
    result = {}
    for role in DISPLAY_ROLES:
        row = rows.get(role)
        result[role] = {
            "connected": bool(row["connected"]) if row else False,
            "shown_revision": row["shown_revision"] if row else None,
            "last_heartbeat_at": row["last_heartbeat_at"] if row else None,
        }
    return result


def get_playback_status(conn: sqlite3.Connection) -> dict[str, Any]:
    st = engine.load_state(conn)

    current = None
    if st.current_item_id is not None:
        item = conn.execute(
            "SELECT * FROM playback_items WHERE id = ?", (st.current_item_id,)
        ).fetchone()
        run = None
        if st.current_run_id is not None:
            run = conn.execute(
                "SELECT * FROM playback_runs WHERE id = ?", (st.current_run_id,)
            ).fetchone()
        if item is not None:
            current = {
                "playback_item_id": item["id"],
                "receipt_order": item["receipt_order"],
                "submission_id": item["submission_id"],
                "result_kind": item["result_kind"],
                "timing_started_at": run["timing_started_at"] if run else None,
                "min_display_seconds": item["min_display_seconds"],
                "played": item["status"] == "played",
            }

    pending = None
    if st.pending_item_id is not None:
        item = conn.execute(
            "SELECT id, receipt_order FROM playback_items WHERE id = ?", (st.pending_item_id,)
        ).fetchone()
        if item is not None:
            ready_roles = [
                row["role"]
                for row in conn.execute(
                    "SELECT role FROM display_clients WHERE connected = 1 AND ready_revision = ?",
                    (st.pending_revision,),
                )
            ]
            pending = {
                "playback_item_id": item["id"],
                "receipt_order": item["receipt_order"],
                "ready_roles": [role for role in DISPLAY_ROLES if role in ready_roles],
            }

    return {
        "phase": st.phase,
        "display_revision": st.display_revision,
        "current": current,
        "pending": pending,
        "clients": _clients(conn),
    }


def get_progress(conn: sqlite3.Connection) -> dict[str, Any]:
    return snapshots.progress(conn)
