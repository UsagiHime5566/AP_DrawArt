"""單螢幕展覽狀態機；所有轉場與輸入鎖都由主機交易管理。"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import datetime
from typing import Any

from backend import db
from backend.api.errors import ApiError
from backend.config import FAILURE_TEXT
from backend.logclient import get_logger
from backend.playback import snapshots
from backend.services import settings, state
from backend.timeutil import now_iso, now_utc, parse_iso, to_iso

log = get_logger("exhibition")
KEY = "exhibition_v2"
HEARTBEAT_SECONDS = 8
STAGES = ("visitor", "ai", "artist")


def load(conn: sqlite3.Connection) -> dict[str, Any]:
    return state.get(conn, KEY) or {
        "mode": "gallery", "revision": 0, "item_id": None,
        "stage": "artist", "kind": None, "owner_submission_id": None,
        "session_id": None, "resume_item_id": None, "started_at": None,
        "durations": [10, 10, 10], "run_id": None,
        "client_id": None, "last_seen": None,
    }


def save(conn: sqlite3.Connection, view: dict[str, Any]) -> None:
    state.set(conn, KEY, view)


def _item(conn: sqlite3.Connection, item_id: int | None) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT p.*, s.dir_path, se.artwork_id, j.status AS ai_status,
                  a.result_file FROM playback_items p
           JOIN submissions s ON s.id = p.submission_id
           JOIN sessions se ON se.id = s.session_id
           LEFT JOIN ai_jobs j ON j.submission_id = s.id
           LEFT JOIN ai_attempts a ON a.id = p.ai_attempt_id WHERE p.id = ?""",
        (item_id,),
    ).fetchone()


def _durations(conn: sqlite3.Connection) -> list[int]:
    _, current = settings.get_current(conn)
    return [int(current[f"{stage}_seconds"]) for stage in STAGES]


def _finish_segment(conn: sqlite3.Connection, view: dict[str, Any], now: datetime, result: str) -> None:
    if view["run_id"] is not None:
        conn.execute(
            "UPDATE exhibition_segments SET ended_at = ?, result = ? WHERE id = ? AND result IS NULL",
            (to_iso(now), result, view["run_id"]),
        )
        view["run_id"] = None


def _stage(conn: sqlite3.Connection, view: dict[str, Any], stage: str) -> None:
    view.update(stage=stage, started_at=None, revision=view["revision"] + 1)
    cursor = conn.execute(
        """INSERT INTO exhibition_segments
           (playback_item_id, kind, stage, revision, seconds) VALUES (?, ?, ?, ?, ?)""",
        (view["item_id"], view["kind"], stage, view["revision"],
         view["durations"][STAGES.index(stage)]),
    )
    view["run_id"] = int(cursor.lastrowid)
    log.info("exhibition.stage", f"切換至{stage}段", playback_item_id=view["item_id"],
             kind=view["kind"], revision=view["revision"])


def _begin_item(conn: sqlite3.Connection, view: dict[str, Any], item_id: int, kind: str) -> None:
    view.update(item_id=item_id, kind=kind, durations=_durations(conn))
    _stage(conn, view, "visitor")


def assert_can_start(conn: sqlite3.Connection) -> None:
    view = load(conn)
    if view["owner_submission_id"] is not None or view["mode"] == "personal":
        raise ApiError(409, "exhibition_locked", "本人的作品尚未完整展示，請稍候")


def begin_drawing(conn: sqlite3.Connection, session_id: str) -> None:
    view = load(conn)
    _finish_segment(conn, view, now_utc(), "interrupted")
    if view["item_id"] is not None:
        view["resume_item_id"] = view["item_id"]
    view.update(mode="drawing", session_id=session_id, item_id=None, kind=None,
                started_at=None, revision=view["revision"] + 1)
    save(conn, view)
    log.info("exhibition.wake", "筆尖接觸，暫停輪播並進入繪圖", session_id=session_id)


def own_submission(conn: sqlite3.Connection, session_id: str, submission_id: str) -> None:
    item = conn.execute(
        "SELECT id FROM playback_items WHERE submission_id = ?", (submission_id,)
    ).fetchone()
    view = load(conn)
    view.update(mode="personal", session_id=session_id, owner_submission_id=submission_id)
    _begin_item(conn, view, item["id"], "personal")
    save(conn, view)
    log.info("exhibition.lock", "開始本人完整首播，鎖定繪圖輸入", submission_id=submission_id)


def end_without_submission(conn: sqlite3.Connection, session_id: str) -> None:
    view = load(conn)
    if view["mode"] == "drawing" and view["session_id"] == session_id:
        view.update(mode="gallery", session_id=None, item_id=None,
                    started_at=None, kind=None, revision=view["revision"] + 1)
        save(conn, view)


def _recent_ids(conn: sqlite3.Connection) -> list[int]:
    _, current = settings.get_current(conn)
    return [row[0] for row in reversed(conn.execute(
        "SELECT id FROM playback_items WHERE status = 'played' ORDER BY receipt_order DESC LIMIT ?",
        (current["gallery_limit"],),
    ).fetchall())]


def _choose_next(conn: sqlite3.Connection, view: dict[str, Any], previous: int | None) -> None:
    pending = conn.execute(
        "SELECT id FROM playback_items WHERE status != 'played' ORDER BY receipt_order LIMIT 1"
    ).fetchone()
    if pending:
        _begin_item(conn, view, pending["id"], "premiere")
        return
    recent = _recent_ids(conn)
    resume = view["resume_item_id"]
    view["resume_item_id"] = None
    if recent:
        if resume in recent:
            selected = resume
        elif previous in recent:
            selected = recent[(recent.index(previous) + 1) % len(recent)]
        else:
            selected = recent[0]
        _begin_item(conn, view, selected, "history")
    else:
        if view["item_id"] is not None or view["kind"] is not None:
            view.update(item_id=None, kind=None, stage="artist", started_at=None,
                        revision=view["revision"] + 1)


def _alive(view: dict[str, Any], now: datetime) -> bool:
    return bool(view["last_seen"] and
                (now - parse_iso(view["last_seen"])).total_seconds() <= HEARTBEAT_SECONDS)


def step(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    now = now or now_utc()
    with db.transaction(conn):
        view = load(conn)
        if view["mode"] == "drawing":
            return
        if view["item_id"] is None:
            _choose_next(conn, view, None)
            save(conn, view)
            return
        item = _item(conn, view["item_id"])
        if item is None:
            # 損壞的本人項目不能靜默解鎖。
            return
        if view["started_at"] is None:
            return
        if not _alive(view, now):
            _finish_segment(conn, view, now, "interrupted")
            _stage(conn, view, view["stage"])
            save(conn, view)
            return
        elapsed = (now - parse_iso(view["started_at"])).total_seconds()
        if elapsed < view["durations"][STAGES.index(view["stage"])]:
            return
        if view["stage"] == "visitor" and item["result_kind"] is None:
            return
        _finish_segment(conn, view, now, "completed")
        if view["stage"] != "artist":
            _stage(conn, view, STAGES[STAGES.index(view["stage"]) + 1])
        else:
            if view["kind"] != "history":
                conn.execute(
                    "UPDATE playback_items SET status = 'played', played_at = ?, updated_at = ? WHERE id = ?",
                    (to_iso(now), to_iso(now), item["id"]),
                )
            if view["mode"] == "personal":
                log.info("exhibition.unlock", "本人三段首播完成，開放新的筆尖接觸",
                         submission_id=view["owner_submission_id"])
            view.update(mode="gallery", owner_submission_id=None, session_id=None)
            _choose_next(conn, view, item["id"])
        save(conn, view)


def presented(conn: sqlite3.Connection, revision: int, client_id: str, visible: bool,
              now: datetime | None = None) -> None:
    now = now or now_utc()
    with db.transaction(conn):
        view = load(conn)
        if revision != view["revision"] or view["mode"] == "drawing":
            return
        if view["client_id"] not in (None, client_id) and _alive(view, now):
            raise ApiError(409, "display_in_use", "已有另一個展覽視窗正在顯示")
        view.update(client_id=client_id, last_seen=to_iso(now))
        if not visible:
            if view["started_at"] is not None:
                _finish_segment(conn, view, now, "interrupted")
                _stage(conn, view, view["stage"])
        elif view["item_id"] is not None and view["started_at"] is None:
            view["started_at"] = to_iso(now)
            conn.execute("UPDATE exhibition_segments SET started_at = ? WHERE id = ?",
                         (to_iso(now), view["run_id"]))
        save(conn, view)


def payload(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    from backend.services import artworks, sessions

    now = now or now_utc()
    view = load(conn)
    item = _item(conn, view["item_id"])
    artwork = artworks.artwork_info(conn, item["artwork_id"]) if item else snapshots.active_artwork(conn)
    url = None
    failure_text = None
    if item:
        if view["stage"] == "visitor":
            url = artworks.media_url(f"{item['dir_path']}/visitor.png", item["content_version"])
        elif view["stage"] == "artist":
            url = artwork["url"]
        elif item["result_kind"] == "generated" and item["result_file"]:
            url = artworks.media_url(item["result_file"], item["content_version"])
        else:
            failure_text = item["failure_text"] or FAILURE_TEXT
    elif artwork:
        url = artwork["url"]
    available = sessions.next_session_available_at(conn)
    active = sessions.get_active_row(conn)
    connected = _alive(view, now)
    return {
        "server_now": to_iso(now), "revision": view["revision"],
        "mode": view["mode"], "stage": view["stage"], "kind": view["kind"],
        "can_start": view["mode"] == "gallery" and not active and not available
                     and state.is_intake_enabled(conn),
        "locked": view["mode"] == "personal", "connected": connected,
        "session_id": view["session_id"], "submission_id": item["submission_id"] if item else None,
        "playback_item_id": view["item_id"], "receipt_order": item["receipt_order"] if item else None,
        "image_url": url, "failure_text": failure_text, "artwork": artwork,
        "started_at": view["started_at"],
        "seconds": view["durations"][STAGES.index(view["stage"])],
        "waiting_for_ai": bool(item and view["stage"] == "visitor" and item["result_kind"] is None),
        "next_session_available_at": available,
        "progress": snapshots.progress(conn),
    }


def recover(conn: sqlite3.Connection) -> None:
    with db.transaction(conn):
        view = load(conn)
        active = conn.execute("SELECT id FROM sessions WHERE status = 'drawing'").fetchone()
        if active is not None and view["mode"] != "personal":
            view.update(mode="drawing", session_id=active["id"])
        elif view["mode"] == "drawing" and active is None:
            view.update(mode="gallery", session_id=None, item_id=None, kind=None)
        if view["run_id"] is not None:
            _finish_segment(conn, view, now_utc(), "interrupted")
        view.update(started_at=None, last_seen=None, client_id=None)
        if view["item_id"] is not None:
            _stage(conn, view, view["stage"])
        save(conn, view)


class ExhibitionController:
    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self._recover)
        self.task = asyncio.create_task(self._run(), name="single-screen-exhibition")

    @staticmethod
    def _recover() -> None:
        with db.connection() as conn:
            recover(conn)

    @staticmethod
    def _step() -> None:
        with db.connection() as conn:
            step(conn)

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._step)
            except Exception:
                log.exception("exhibition.error", "展覽狀態推進失敗")
            await asyncio.sleep(0.2)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await asyncio.to_thread(self._recover)


controller = ExhibitionController()
