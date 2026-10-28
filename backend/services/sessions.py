"""場次生命週期：建立、查詢、放棄，以及給平板的 SessionView。"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from backend.api.errors import ApiError
from backend.db import transaction
from backend.logclient import get_logger
from backend.services import artworks, settings, state
from backend.timeutil import add_seconds, now_iso, now_utc, to_iso

log = get_logger("web")

END_REASONS_ABANDON = ("draft_lost", "staff")


def get_session_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ApiError(404, "session_not_found", "找不到場次")
    return row


def get_active_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sessions WHERE status = 'drawing'").fetchone()


def queue_ahead(conn: sqlite3.Connection, receipt_order: int) -> int:
    """收件順序在前、尚未播完的作品件數。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM playback_items WHERE status != 'played' AND receipt_order < ?",
        (receipt_order,),
    ).fetchone()
    return int(row["n"])


def session_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    submission = None
    if row["status"] == "submitted":
        sub = conn.execute(
            "SELECT id, receipt_order FROM submissions WHERE session_id = ?", (row["id"],)
        ).fetchone()
        if sub is not None:
            submission = {"id": sub["id"], "queue_ahead": queue_ahead(conn, sub["receipt_order"])}
    return {
        "id": row["id"],
        "status": row["status"],
        "artwork": artworks.artwork_info(conn, row["artwork_id"]),
        "settings": settings.public_subset(settings.get_version(conn, row["settings_version_id"])),
        "started_at": row["started_at"],
        "deadline_at": row["deadline_at"],
        "ended_at": row["ended_at"],
        "next_session_available_at": row["next_session_available_at"],
        "submission": submission,
    }


def next_session_available_at(conn: sqlite3.Connection) -> str | None:
    """最近一次換場等待的結束時間；已經過了則回傳 None。"""
    row = conn.execute(
        "SELECT MAX(next_session_available_at) AS t FROM sessions WHERE status NOT IN ('drawing', 'submitted')"
    ).fetchone()
    value = row["t"]
    if value is None or value <= now_iso():
        return None
    return value


def current_state(conn: sqlite3.Connection) -> dict[str, Any]:
    active = get_active_row(conn)
    return {
        "intake_enabled": state.is_intake_enabled(conn),
        "next_session_available_at": next_session_available_at(conn),
        "active_session": session_view(conn, active) if active else None,
    }


def create_session(conn: sqlite3.Connection, tablet_id: str | None) -> sqlite3.Row:
    artwork = artworks.sync_active_artwork(conn)
    if artwork is None:
        raise ApiError(503, "artwork_unavailable", "目前沒有可用的藝術家原圖")

    with transaction(conn):
        from backend import exhibition

        exhibition.assert_can_start(conn)
        active = get_active_row(conn)
        if active is not None:
            raise ApiError(409, "session_active", "已有進行中的場次", {"session_id": active["id"]})
        if not state.is_intake_enabled(conn):
            raise ApiError(409, "intake_paused", "目前暫停接待")
        available_at = next_session_available_at(conn)
        if available_at is not None:
            raise ApiError(
                409, "turnaround", "換場等待中", {"next_session_available_at": available_at}
            )

        version_id, data = settings.get_current(conn)
        started = now_utc()
        session_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO sessions (id, status, artwork_id, settings_version_id, tablet_id,
                                  started_at, deadline_at)
            VALUES (?, 'drawing', ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                artwork["id"],
                version_id,
                tablet_id,
                to_iso(started),
                to_iso(add_seconds(started, data["drawing_duration_seconds"])),
            ),
        )
        exhibition.begin_drawing(conn, session_id)
    log.info(
        "session.start",
        f"開始場次，作畫 {data['drawing_duration_seconds']} 秒",
        session_id=session_id,
        artwork_id=artwork["id"],
        settings_version_id=version_id,
    )
    return get_session_row(conn, session_id)


def end_fields(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, str]:
    """結束場次時共用的時間欄位：結束時間與依綁定設定計算的下場開放時間。"""
    ended = now_utc()
    turnaround = settings.get_version(conn, row["settings_version_id"])["turnaround_seconds"]
    return {
        "ended_at": to_iso(ended),
        "next_session_available_at": to_iso(add_seconds(ended, turnaround)),
    }


def abandon_session(conn: sqlite3.Connection, session_id: str, reason: str) -> sqlite3.Row:
    if reason not in END_REASONS_ABANDON:
        raise ApiError(422, "validation_error", "未知的放棄原因")
    with transaction(conn):
        row = get_session_row(conn, session_id)
        if row["status"] == "abandoned":
            return row
        if row["status"] != "drawing":
            raise ApiError(
                409, "session_closed", "場次已結束", {"session": session_view(conn, row)}
            )
        fields = end_fields(conn, row)
        conn.execute(
            """
            UPDATE sessions SET status = 'abandoned', ended_at = ?, end_reason = ?,
                                next_session_available_at = ?
            WHERE id = ?
            """,
            (fields["ended_at"], reason, fields["next_session_available_at"], session_id),
        )
        from backend import exhibition

        exhibition.end_without_submission(conn, session_id)
    log.info("session.abandoned", f"放棄場次（{reason}）", session_id=session_id)
    return get_session_row(conn, session_id)
