"""播放推進邏輯（同步）。

控制器每一輪在背景執行緒呼叫 step()：依資料庫狀態與三個展示角色的回報，
決定計時、標記已播、commit 與 prepare，回傳要廣播給展示頁的訊息。
所有判斷都只依賴傳入的 now，測試可直接呼叫並指定時間。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from backend.config import DISPLAY_ROLES
from backend.db import transaction
from backend.logclient import get_logger
from backend.playback import snapshots
from backend.services import settings
from backend.timeutil import parse_iso, to_iso

log = get_logger("playback")

HEARTBEAT_TIMEOUT_SECONDS = 15.0
SWITCH_DELAY_SECONDS = 0.4

# 快照無法組成的項目只記錄一次錯誤，避免每輪重複寫日誌
_reported_broken_items: set[int] = set()


@dataclass
class RoleView:
    """某個展示角色目前的連線與回報狀態（由控制器在記憶體中維護）。"""

    connected: bool = False
    ready_revision: int | None = None
    shown_revision: int | None = None
    shown_at: datetime | None = None
    last_heartbeat: datetime | None = None
    client_id: str | None = None
    remote_addr: str | None = None

    def alive(self, now: datetime) -> bool:
        if not self.connected or self.last_heartbeat is None:
            return False
        return (now - self.last_heartbeat).total_seconds() <= HEARTBEAT_TIMEOUT_SECONDS


@dataclass
class DisplayState:
    display_revision: int = 0
    phase: str = "idle"
    current_item_id: int | None = None
    current_revision: int | None = None
    current_run_id: int | None = None
    pending_item_id: int | None = None
    pending_revision: int | None = None
    switch_at: str | None = None


def load_state(conn: sqlite3.Connection) -> DisplayState:
    row = conn.execute("SELECT * FROM display_state WHERE id = 1").fetchone()
    if row is None:
        return DisplayState()
    return DisplayState(
        display_revision=row["display_revision"],
        phase=row["phase"],
        current_item_id=row["current_item_id"],
        current_revision=row["current_revision"],
        current_run_id=row["current_run_id"],
        pending_item_id=row["pending_item_id"],
        pending_revision=row["pending_revision"],
        switch_at=row["switch_at"],
    )


def save_state(conn: sqlite3.Connection, st: DisplayState, now: datetime) -> None:
    if st.pending_item_id is not None:
        st.phase = "prepare"
    elif st.current_item_id is not None:
        st.phase = "playing"
    else:
        st.phase = "idle"
    conn.execute(
        """
        INSERT INTO display_state (
            id, display_revision, phase, current_item_id, current_revision, current_run_id,
            pending_item_id, pending_revision, switch_at, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_revision = excluded.display_revision,
            phase = excluded.phase,
            current_item_id = excluded.current_item_id,
            current_revision = excluded.current_revision,
            current_run_id = excluded.current_run_id,
            pending_item_id = excluded.pending_item_id,
            pending_revision = excluded.pending_revision,
            switch_at = excluded.switch_at,
            updated_at = excluded.updated_at
        """,
        (
            st.display_revision,
            st.phase,
            st.current_item_id,
            st.current_revision,
            st.current_run_id,
            st.pending_item_id,
            st.pending_revision,
            st.switch_at,
            to_iso(now),
        ),
    )


def save_clients(conn: sqlite3.Connection, roles: Mapping[str, RoleView], now: datetime) -> None:
    for role, view in roles.items():
        conn.execute(
            """
            INSERT INTO display_clients (
                role, client_id, connected, shown_revision, ready_revision,
                last_heartbeat_at, remote_addr, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(role) DO UPDATE SET
                client_id = excluded.client_id,
                connected = excluded.connected,
                shown_revision = excluded.shown_revision,
                ready_revision = excluded.ready_revision,
                last_heartbeat_at = excluded.last_heartbeat_at,
                remote_addr = excluded.remote_addr,
                updated_at = excluded.updated_at
            """,
            (
                role,
                view.client_id,
                1 if view.connected else 0,
                view.shown_revision,
                view.ready_revision,
                to_iso(view.last_heartbeat) if view.last_heartbeat else None,
                view.remote_addr,
                to_iso(now),
            ),
        )


def _item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM playback_items WHERE id = ?", (item_id,)).fetchone()


def _run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM playback_runs WHERE id = ?", (run_id,)).fetchone()


def _not_presenting(roles: Mapping[str, RoleView], revision: int | None, now: datetime) -> list[str]:
    """回傳沒有正在顯示指定版本的角色與原因。"""
    reasons = []
    for role in DISPLAY_ROLES:
        view = roles.get(role)
        if view is None or not view.connected:
            reasons.append(f"{role}:disconnected")
        elif not view.alive(now):
            reasons.append(f"{role}:heartbeat_timeout")
        elif view.shown_revision != revision:
            reasons.append(f"{role}:revision_{view.shown_revision}")
    return reasons


def _all_ready(roles: Mapping[str, RoleView], revision: int | None, now: datetime) -> bool:
    return all(
        (view := roles.get(role)) is not None and view.alive(now) and view.ready_revision == revision
        for role in DISPLAY_ROLES
    )


def _new_run(
    conn: sqlite3.Connection,
    item_id: int,
    revision: int,
    min_seconds: int,
    committed_at: str | None,
    switch_at: str | None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO playback_runs (
            playback_item_id, display_revision, min_display_seconds, committed_at, switch_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (item_id, revision, min_seconds, committed_at, switch_at),
    )
    return int(cursor.lastrowid)


def _advance_current(
    conn: sqlite3.Connection, st: DisplayState, roles: Mapping[str, RoleView], now: datetime
) -> None:
    """目前作品：三個角色都顯示後開始計時；中斷時重計；滿最低秒數標記已播。"""
    if st.current_item_id is None or st.current_run_id is None:
        return
    item = _item(conn, st.current_item_id)
    run = _run(conn, st.current_run_id)
    if item is None or run is None or item["status"] == "played":
        return

    missing = _not_presenting(roles, st.current_revision, now)
    now_iso = to_iso(now)

    if run["timing_started_at"] is None:
        if missing:
            return
        shown_times = [roles[role].shown_at for role in DISPLAY_ROLES if roles[role].shown_at]
        started = min(max(shown_times), now) if shown_times else now
        conn.execute(
            """
            UPDATE playback_runs SET timing_started_at = ?, visitor_presented_at = ?,
                   ai_presented_at = ?, artist_presented_at = ?
            WHERE id = ?
            """,
            (
                to_iso(started),
                *(to_iso(roles[role].shown_at or now) for role in DISPLAY_ROLES),
                run["id"],
            ),
        )
        log.info(
            "playback.timing_start",
            f"三個螢幕皆已顯示，開始計算最低展示時間 {run['min_display_seconds']} 秒",
            playback_item_id=item["id"],
            receipt_order=item["receipt_order"],
            revision=st.current_revision,
        )
        return

    if missing:
        conn.execute(
            "UPDATE playback_runs SET ended_at = ?, result = 'interrupted', note = ? WHERE id = ?",
            (now_iso, ",".join(missing), run["id"]),
        )
        st.current_run_id = _new_run(
            conn,
            item["id"],
            run["display_revision"],
            run["min_display_seconds"],
            run["committed_at"],
            run["switch_at"],
        )
        log.warning(
            "playback.interrupted",
            f"展示中斷，暫停計時，恢復後重新計算（{', '.join(missing)}）",
            playback_item_id=item["id"],
            receipt_order=item["receipt_order"],
            revision=st.current_revision,
        )
        return

    elapsed = (now - parse_iso(run["timing_started_at"])).total_seconds()
    if elapsed >= run["min_display_seconds"]:
        conn.execute(
            "UPDATE playback_items SET status = 'played', played_at = ?, updated_at = ? WHERE id = ?",
            (now_iso, now_iso, item["id"]),
        )
        conn.execute(
            "UPDATE playback_runs SET ended_at = ?, result = 'completed' WHERE id = ?",
            (now_iso, run["id"]),
        )
        log.info(
            "playback.played",
            f"作品已完成展示（{elapsed:.1f} 秒）",
            playback_item_id=item["id"],
            receipt_order=item["receipt_order"],
            submission_id=item["submission_id"],
        )


def _try_commit(
    conn: sqlite3.Connection,
    st: DisplayState,
    roles: Mapping[str, RoleView],
    now: datetime,
    messages: list[dict[str, Any]],
) -> None:
    """待切換作品：三個角色都預載完成，且目前作品已播畢，才整組切換。"""
    if st.pending_item_id is None:
        return
    if st.current_item_id is not None:
        current = _item(conn, st.current_item_id)
        if current is not None and current["status"] != "played":
            return
    if not _all_ready(roles, st.pending_revision, now):
        return

    pending = _item(conn, st.pending_item_id)
    if pending is None:
        st.pending_item_id = None
        st.pending_revision = None
        return

    _, current_settings = settings.get_current(conn)
    min_seconds = int(current_settings["display_min_seconds"])
    now_iso = to_iso(now)
    switch_at = to_iso(now + timedelta(seconds=SWITCH_DELAY_SECONDS))
    conn.execute(
        """
        UPDATE playback_items SET status = 'playing', min_display_seconds = ?, updated_at = ?
        WHERE id = ?
        """,
        (min_seconds, now_iso, pending["id"]),
    )
    run_id = _new_run(conn, pending["id"], st.pending_revision, min_seconds, now_iso, switch_at)

    st.current_item_id = pending["id"]
    st.current_revision = st.pending_revision
    st.current_run_id = run_id
    st.pending_item_id = None
    st.pending_revision = None
    st.switch_at = switch_at
    messages.append({"type": "commit", "revision": st.current_revision, "switch_at": switch_at})
    log.info(
        "playback.commit",
        f"三螢幕整組切換（最低展示 {min_seconds} 秒）",
        playback_item_id=pending["id"],
        receipt_order=pending["receipt_order"],
        revision=st.current_revision,
    )


def _try_prepare(
    conn: sqlite3.Connection, st: DisplayState, now: datetime, messages: list[dict[str, Any]]
) -> None:
    """依收件順序取下一件未播作品；它仍在等待 AI 時不跳過、不超車。"""
    if st.pending_item_id is not None:
        return
    row = conn.execute(
        """
        SELECT id, status, receipt_order FROM playback_items
        WHERE status != 'played' AND (? IS NULL OR id != ?)
        ORDER BY receipt_order
        LIMIT 1
        """,
        (st.current_item_id, st.current_item_id),
    ).fetchone()
    if row is None or row["status"] == "waiting_for_ai":
        return

    revision = st.display_revision + 1
    snapshot = snapshots.item_snapshot(conn, row["id"], revision)
    if snapshot is None:
        if row["id"] not in _reported_broken_items:
            _reported_broken_items.add(row["id"])
            log.error(
                "playback.prepare",
                "無法組成播放快照（作品、場次或原圖資料缺漏）",
                playback_item_id=row["id"],
                receipt_order=row["receipt_order"],
            )
        return

    conn.execute(
        "UPDATE playback_items SET status = 'preparing', updated_at = ? WHERE id = ?",
        (to_iso(now), row["id"]),
    )
    st.display_revision = revision
    st.pending_item_id = row["id"]
    st.pending_revision = revision
    messages.append({"type": "prepare", "snapshot": snapshot})
    log.info(
        "playback.prepare",
        "準備下一件作品，通知三個螢幕預載",
        playback_item_id=row["id"],
        receipt_order=row["receipt_order"],
        revision=revision,
        kind=snapshot["ai"]["type"],
    )


def step(conn: sqlite3.Connection, roles: Mapping[str, RoleView], now: datetime) -> list[dict[str, Any]]:
    """推進一輪，回傳要廣播給所有展示頁的訊息（不含 server_now）。"""
    messages: list[dict[str, Any]] = []
    with transaction(conn):
        st = load_state(conn)
        before = DisplayState(**vars(st))
        _advance_current(conn, st, roles, now)
        _try_commit(conn, st, roles, now, messages)
        _try_prepare(conn, st, now, messages)
        if vars(st) != vars(before):
            save_state(conn, st, now)
    return messages


def recover(conn: sqlite3.Connection, now: datetime) -> None:
    """主機重啟：未完成的播放改回 ready 重新播放；畫面恢復為最後一件已播作品。"""
    now_iso = to_iso(now)
    with transaction(conn):
        st = load_state(conn)
        stuck = conn.execute(
            "SELECT id, receipt_order FROM playback_items WHERE status IN ('preparing', 'playing')"
        ).fetchall()
        for row in stuck:
            conn.execute(
                """
                UPDATE playback_items SET status = 'ready', min_display_seconds = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now_iso, row["id"]),
            )
            log.info(
                "playback.interrupted",
                "主機重啟，未完成的播放將重新播放",
                playback_item_id=row["id"],
                receipt_order=row["receipt_order"],
            )
        conn.execute(
            """
            UPDATE playback_runs SET ended_at = ?, result = 'interrupted', note = 'restart'
            WHERE ended_at IS NULL
            """,
            (now_iso,),
        )

        current = _item(conn, st.current_item_id) if st.current_item_id is not None else None
        if current is None or current["status"] != "played":
            last_played = conn.execute(
                """
                SELECT id FROM playback_items WHERE status = 'played'
                ORDER BY receipt_order DESC LIMIT 1
                """
            ).fetchone()
            if last_played is not None:
                st.display_revision += 1
                st.current_item_id = last_played["id"]
                st.current_revision = st.display_revision
            else:
                st.current_item_id = None
                st.current_revision = None
        st.current_run_id = None
        st.pending_item_id = None
        st.pending_revision = None
        st.switch_at = None
        save_state(conn, st, now)
        conn.execute("UPDATE display_clients SET connected = 0, updated_at = ?", (now_iso,))


def current_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    """GET /api/display/current 與 hello 共用：目前畫面、待切換畫面與生成進度。"""
    st = load_state(conn)
    current = None
    if st.current_item_id is not None and st.current_revision is not None:
        current = snapshots.item_snapshot(conn, st.current_item_id, st.current_revision)
    if current is None:
        current = snapshots.standby_snapshot(conn)
    pending = None
    if st.pending_item_id is not None and st.pending_revision is not None:
        pending = snapshots.item_snapshot(conn, st.pending_item_id, st.pending_revision)
    return {"current": current, "pending": pending, "progress": snapshots.progress(conn)}


def hello_payload(conn: sqlite3.Connection, role: str) -> dict[str, Any]:
    return {"type": "hello", "role": role, **current_payload(conn)}
