"""播放控制器測試：engine 推進邏輯（指定時間）與 WebSocket 協定（實際連線）。"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import anyio
import pytest
from starlette.websockets import WebSocketDisconnect

from backend import db
from backend.config import DISPLAY_ROLES, FAILURE_TEXT
from backend.db import transaction
from backend.playback import engine, snapshots, status
from backend.playback.engine import RoleView
from backend.services import settings, state
from backend.timeutil import now_utc, to_iso

# ---------- 測試資料 ----------


def seed_artwork(conn) -> int:
    cursor = conn.execute(
        "INSERT INTO artworks (sha256, width, height, file_path, created_at) VALUES (?, 1600, 1132, '', ?)",
        (uuid.uuid4().hex, to_iso(now_utc())),
    )
    artwork_id = int(cursor.lastrowid)
    conn.execute(
        "UPDATE artworks SET file_path = ? WHERE id = ?",
        (f"artworks/{artwork_id}/original.webp", artwork_id),
    )
    state.set(conn, "active_artwork_id", artwork_id)
    return artwork_id


def add_item(conn, artwork_id: int) -> dict:
    """模擬收件交易：場次、作品、queued 工作與 waiting_for_ai 待播項。"""
    now = to_iso(now_utc())
    order = conn.execute("SELECT COALESCE(MAX(receipt_order), 0) + 1 FROM submissions").fetchone()[0]
    version_id, _ = settings.get_current(conn)
    session_id, submission_id = uuid.uuid4().hex, uuid.uuid4().hex
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO sessions (id, status, artwork_id, settings_version_id, started_at,
                                  deadline_at, ended_at, end_reason)
            VALUES (?, 'submitted', ?, ?, ?, ?, ?, 'manual')
            """,
            (session_id, artwork_id, version_id, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO submissions (id, session_id, receipt_order, submission_key, content_sha256,
                                     dir_path, width, height, received_at)
            VALUES (?, ?, ?, ?, ?, ?, 1920, 1080, ?)
            """,
            (submission_id, session_id, order, uuid.uuid4().hex, "0" * 64, f"submissions/{submission_id}", now),
        )
        job_id = conn.execute(
            """
            INSERT INTO ai_jobs (submission_id, receipt_order, settings_version_id, status,
                                 created_at, updated_at)
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (submission_id, order, version_id, now, now),
        ).lastrowid
        item_id = conn.execute(
            """
            INSERT INTO playback_items (submission_id, receipt_order, status, created_at, updated_at)
            VALUES (?, ?, 'waiting_for_ai', ?, ?)
            """,
            (submission_id, order, now, now),
        ).lastrowid
    return {
        "item_id": item_id,
        "job_id": job_id,
        "order": order,
        "submission_id": submission_id,
        "dir": f"submissions/{submission_id}",
    }


def finish_generated(conn, item: dict, duration_ms: int = 20_000) -> str:
    attempt_id = uuid.uuid4().hex
    now = to_iso(now_utc())
    result_file = f"{item['dir']}/ai/{attempt_id}/ai-result.png"
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO ai_attempts (id, ai_job_id, status, model, quality, size, prompt,
                                     timeout_seconds, dir_path, result_file, started_at,
                                     finished_at, duration_ms)
            VALUES (?, ?, 'succeeded', 'gpt-image-2.5-sunburst', 'medium', '2048x1152', 'p',
                    180, ?, ?, ?, ?, ?)
            """,
            (attempt_id, item["job_id"], f"{item['dir']}/ai/{attempt_id}", result_file, now, now, duration_ms),
        )
        conn.execute(
            """
            UPDATE ai_jobs SET status = 'succeeded', current_attempt_id = ?, finished_at = ?,
                               updated_at = ? WHERE id = ?
            """,
            (attempt_id, now, now, item["job_id"]),
        )
        conn.execute(
            """
            UPDATE playback_items SET status = 'ready', result_kind = 'generated', ai_attempt_id = ?,
                   content_version = content_version + 1, updated_at = ? WHERE id = ?
            """,
            (attempt_id, now, item["item_id"]),
        )
    return result_file


def finish_failed(conn, item: dict) -> None:
    now = to_iso(now_utc())
    with transaction(conn):
        conn.execute(
            "UPDATE ai_jobs SET status = 'failed', error_code = 'timeout', updated_at = ? WHERE id = ?",
            (now, item["job_id"]),
        )
        conn.execute(
            """
            UPDATE playback_items SET status = 'ready', result_kind = 'failure_card',
                   failure_text = ?, content_version = content_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (FAILURE_TEXT, now, item["item_id"]),
        )


def item_row(conn, item: dict):
    return conn.execute("SELECT * FROM playback_items WHERE id = ?", (item["item_id"],)).fetchone()


def runs_of(conn, item: dict) -> list:
    return conn.execute(
        "SELECT * FROM playback_runs WHERE playback_item_id = ? ORDER BY id", (item["item_id"],)
    ).fetchall()


# ---------- engine（指定時間，不經網路） ----------


def all_roles(now: datetime) -> dict[str, RoleView]:
    return {role: RoleView(connected=True, last_heartbeat=now) for role in DISPLAY_ROLES}


def mark_ready(roles: dict[str, RoleView], revision: int, now: datetime) -> None:
    for view in roles.values():
        view.ready_revision = revision
        view.last_heartbeat = now


def mark_presented(roles: dict[str, RoleView], revision: int, now: datetime) -> None:
    for view in roles.values():
        view.shown_revision = revision
        view.shown_at = now
        view.last_heartbeat = now


def tick(conn, roles: dict[str, RoleView], now: datetime) -> list[dict]:
    """所有連線中的角色送出心跳後推進一輪。"""
    for view in roles.values():
        if view.connected:
            view.last_heartbeat = now
    return engine.step(conn, roles, now)


def kinds(messages: list[dict]) -> list[str]:
    return [message["type"] for message in messages]


@pytest.fixture
def clock() -> Callable[[float], datetime]:
    start = now_utc()
    return lambda seconds: start + timedelta(seconds=seconds)


@pytest.fixture
def artwork_id(db_conn) -> int:
    artwork = seed_artwork(db_conn)
    engine.recover(db_conn, now_utc())
    return artwork


def test_standby_when_nothing_received(db_conn, artwork_id, clock):
    assert engine.step(db_conn, all_roles(clock(0)), clock(0)) == []
    payload = engine.current_payload(db_conn)
    assert payload["current"] == {
        "revision": 0,
        "kind": "standby",
        "playback_item_id": None,
        "receipt_order": None,
        "visitor": None,
        "ai": {"type": "standby"},
        "artist": {
            "id": artwork_id,
            "url": f"/media/artworks/{artwork_id}/original.webp?v={artwork_id}",
            "width": 1600,
            "height": 1132,
        },
    }
    assert payload["pending"] is None
    assert payload["progress"] == {
        "active": False,
        "started_at": None,
        "estimate_seconds": 60,
        "queued_after": 0,
    }


def test_snapshots_for_generated_and_failure_card(db_conn, artwork_id, clock):
    first, second = add_item(db_conn, artwork_id), add_item(db_conn, artwork_id)
    result_file = finish_generated(db_conn, first)
    finish_failed(db_conn, second)

    messages = engine.step(db_conn, all_roles(clock(0)), clock(0))
    assert kinds(messages) == ["prepare"]
    snapshot = messages[0]["snapshot"]
    assert snapshot["revision"] == 1
    assert snapshot["kind"] == "item"
    assert snapshot["playback_item_id"] == first["item_id"]
    assert snapshot["visitor"] == {"url": f"/media/{first['dir']}/visitor.png?v=1"}
    assert snapshot["ai"] == {"type": "image", "url": f"/media/{result_file}?v=1"}
    assert snapshot["artist"]["id"] == artwork_id

    failure = snapshots.item_snapshot(db_conn, second["item_id"], 7)
    assert failure["revision"] == 7
    assert failure["ai"] == {"type": "failure", "text": FAILURE_TEXT}
    assert failure["visitor"] == {"url": f"/media/{second['dir']}/visitor.png?v=1"}


def test_fifo_waits_for_ai_without_overtaking(db_conn, artwork_id, clock):
    a, b, c = (add_item(db_conn, artwork_id) for _ in range(3))
    finish_generated(db_conn, a)
    finish_generated(db_conn, c)  # b 仍在等待 AI
    roles = all_roles(clock(0))

    assert kinds(tick(db_conn, roles, clock(0))) == ["prepare"]
    mark_ready(roles, 1, clock(0.1))
    assert kinds(tick(db_conn, roles, clock(0.1))) == ["commit"]  # b 未就緒，不準備 c
    mark_presented(roles, 1, clock(0.5))
    assert tick(db_conn, roles, clock(0.6)) == []
    assert tick(db_conn, roles, clock(10.6)) == []
    assert item_row(db_conn, a)["status"] == "played"
    assert item_row(db_conn, c)["status"] == "ready"
    assert engine.load_state(db_conn).pending_item_id is None
    assert engine.load_state(db_conn).current_item_id == a["item_id"]  # 維持目前畫面

    finish_failed(db_conn, b)
    messages = tick(db_conn, roles, clock(11))
    assert kinds(messages) == ["prepare"]
    assert messages[0]["snapshot"]["playback_item_id"] == b["item_id"]
    assert messages[0]["snapshot"]["ai"]["type"] == "failure"

    mark_ready(roles, 2, clock(11.1))
    messages = tick(db_conn, roles, clock(11.1))
    assert kinds(messages) == ["commit", "prepare"]
    assert messages[1]["snapshot"]["playback_item_id"] == c["item_id"]
    mark_presented(roles, 2, clock(11.5))
    tick(db_conn, roles, clock(11.6))
    mark_ready(roles, 3, clock(12))
    assert kinds(tick(db_conn, roles, clock(21.6))) == ["commit"]

    played = db_conn.execute(
        "SELECT receipt_order FROM playback_items WHERE status = 'played' ORDER BY played_at"
    ).fetchall()
    assert [row[0] for row in played] == [a["order"], b["order"]]
    assert item_row(db_conn, c)["status"] == "playing"


def test_commit_waits_for_min_display_time(db_conn, artwork_id, clock):
    a, b = add_item(db_conn, artwork_id), add_item(db_conn, artwork_id)
    finish_generated(db_conn, a)
    finish_generated(db_conn, b)
    roles = all_roles(clock(0))

    tick(db_conn, roles, clock(0))
    mark_ready(roles, 1, clock(0.1))
    assert kinds(tick(db_conn, roles, clock(0.1))) == ["commit", "prepare"]
    mark_presented(roles, 1, clock(0.2))
    tick(db_conn, roles, clock(0.3))
    assert runs_of(db_conn, a)[0]["timing_started_at"] == to_iso(clock(0.2))

    # 播放中修改最低秒數，只影響之後開始的作品
    with transaction(db_conn):
        settings.update(db_conn, {"display_min_seconds": 3})

    mark_ready(roles, 2, clock(1))
    assert tick(db_conn, roles, clock(5)) == []
    assert tick(db_conn, roles, clock(10.1)) == []
    assert item_row(db_conn, a)["status"] == "playing"
    assert kinds(tick(db_conn, roles, clock(10.3))) == ["commit"]
    assert item_row(db_conn, a)["status"] == "played"
    assert item_row(db_conn, a)["min_display_seconds"] == 10
    assert item_row(db_conn, b)["status"] == "playing"
    assert item_row(db_conn, b)["min_display_seconds"] == 3


def test_disconnect_pauses_and_restarts_full_timing(db_conn, artwork_id, clock):
    a = add_item(db_conn, artwork_id)
    finish_generated(db_conn, a)
    roles = all_roles(clock(0))
    tick(db_conn, roles, clock(0))
    mark_ready(roles, 1, clock(0.1))
    tick(db_conn, roles, clock(0.1))
    mark_presented(roles, 1, clock(0.5))
    tick(db_conn, roles, clock(0.6))
    tick(db_conn, roles, clock(4))

    roles["ai"].connected = False
    tick(db_conn, roles, clock(5))
    tick(db_conn, roles, clock(20))
    assert item_row(db_conn, a)["status"] == "playing"

    roles["ai"] = RoleView(connected=True, last_heartbeat=clock(20))
    tick(db_conn, roles, clock(20.5))  # 重連但尚未顯示，不計時
    roles["ai"].shown_revision = 1
    roles["ai"].shown_at = clock(21)
    tick(db_conn, roles, clock(21.1))
    assert tick(db_conn, roles, clock(30.9)) == []
    assert item_row(db_conn, a)["status"] == "playing"
    tick(db_conn, roles, clock(31.1))
    assert item_row(db_conn, a)["status"] == "played"

    runs = runs_of(db_conn, a)
    assert [run["result"] for run in runs] == ["interrupted", "completed"]
    assert runs[0]["note"] == "ai:disconnected"
    assert runs[1]["timing_started_at"] == to_iso(clock(21))


def test_stale_heartbeat_or_wrong_revision_interrupts(db_conn, artwork_id, clock):
    with transaction(db_conn):
        settings.update(db_conn, {"display_min_seconds": 60})
    a = add_item(db_conn, artwork_id)
    finish_generated(db_conn, a)
    roles = all_roles(clock(0))
    tick(db_conn, roles, clock(0))
    mark_ready(roles, 1, clock(0.1))
    tick(db_conn, roles, clock(0.1))
    mark_presented(roles, 1, clock(0.5))
    tick(db_conn, roles, clock(0.6))

    # ai 停止心跳超過 15 秒
    roles["visitor"].last_heartbeat = clock(16)
    roles["artist"].last_heartbeat = clock(16)
    engine.step(db_conn, roles, clock(16))
    assert item_row(db_conn, a)["status"] == "playing"
    runs = runs_of(db_conn, a)
    assert (runs[0]["result"], runs[0]["note"]) == ("interrupted", "ai:heartbeat_timeout")

    # 恢復後重新計時，之後又有角色回報其他版本
    mark_presented(roles, 1, clock(17))
    tick(db_conn, roles, clock(17.1))
    assert runs_of(db_conn, a)[1]["timing_started_at"] == to_iso(clock(17))
    roles["artist"].shown_revision = 99
    tick(db_conn, roles, clock(18))
    runs = runs_of(db_conn, a)
    assert (runs[1]["result"], runs[1]["note"]) == ("interrupted", "artist:revision_99")
    assert runs[2]["timing_started_at"] is None
    assert item_row(db_conn, a)["status"] == "playing"


def test_restart_replays_unfinished_item(db_conn, artwork_id, clock):
    a, b = add_item(db_conn, artwork_id), add_item(db_conn, artwork_id)
    finish_generated(db_conn, a)
    finish_generated(db_conn, b)
    roles = all_roles(clock(0))
    tick(db_conn, roles, clock(0))
    mark_ready(roles, 1, clock(0.1))
    tick(db_conn, roles, clock(0.1))
    mark_presented(roles, 1, clock(0.2))
    tick(db_conn, roles, clock(0.3))
    mark_ready(roles, 2, clock(1))
    assert kinds(tick(db_conn, roles, clock(10.5))) == ["commit"]  # a 播畢、b 開始
    mark_presented(roles, 2, clock(11))
    tick(db_conn, roles, clock(11.1))
    engine.save_clients(db_conn, roles, clock(11.1))

    engine.recover(db_conn, clock(12))

    assert item_row(db_conn, a)["status"] == "played"
    assert item_row(db_conn, b)["status"] == "ready"
    assert item_row(db_conn, b)["min_display_seconds"] is None
    assert runs_of(db_conn, b)[-1]["result"] == "interrupted"
    assert runs_of(db_conn, b)[-1]["note"] == "restart"
    st = engine.load_state(db_conn)
    assert (st.current_item_id, st.current_revision, st.pending_item_id) == (a["item_id"], 3, None)
    assert engine.current_payload(db_conn)["current"]["playback_item_id"] == a["item_id"]
    assert all(row[0] == 0 for row in db_conn.execute("SELECT connected FROM display_clients"))

    roles = all_roles(clock(13))
    messages = tick(db_conn, roles, clock(13))
    assert kinds(messages) == ["prepare"]
    assert messages[0]["snapshot"]["playback_item_id"] == b["item_id"]
    assert messages[0]["snapshot"]["revision"] == 4


def test_restart_without_played_item_returns_to_standby(db_conn, artwork_id, clock):
    a = add_item(db_conn, artwork_id)
    finish_generated(db_conn, a)
    roles = all_roles(clock(0))
    tick(db_conn, roles, clock(0))
    mark_ready(roles, 1, clock(0.1))
    tick(db_conn, roles, clock(0.1))

    engine.recover(db_conn, clock(1))

    assert item_row(db_conn, a)["status"] == "ready"
    assert engine.load_state(db_conn).current_item_id is None
    assert engine.current_payload(db_conn)["current"]["kind"] == "standby"


def test_progress_estimate_and_queue(db_conn, artwork_id):
    items = [add_item(db_conn, artwork_id) for _ in range(6)]
    for item, duration in zip(items[:3], (10_000, 20_000, 40_000)):
        finish_generated(db_conn, item, duration_ms=duration)
    started_at = to_iso(now_utc())
    db_conn.execute(
        "UPDATE ai_jobs SET status = 'running', started_at = ? WHERE id = ?",
        (started_at, items[3]["job_id"]),
    )
    assert status.get_progress(db_conn) == {
        "active": True,
        "started_at": started_at,
        "estimate_seconds": 20,
        "queued_after": 2,
    }


def test_playback_status_shape(db_conn, artwork_id, clock):
    a, b = add_item(db_conn, artwork_id), add_item(db_conn, artwork_id)
    finish_generated(db_conn, a)
    finish_generated(db_conn, b)
    roles = all_roles(clock(0))
    tick(db_conn, roles, clock(0))
    mark_ready(roles, 1, clock(0.1))
    tick(db_conn, roles, clock(0.1))
    mark_presented(roles, 1, clock(0.2))
    tick(db_conn, roles, clock(0.3))
    roles["visitor"].ready_revision = 2
    engine.save_clients(db_conn, roles, clock(0.3))

    result = status.get_playback_status(db_conn)
    assert result["phase"] == "prepare"
    assert result["display_revision"] == 2
    assert result["current"] == {
        "playback_item_id": a["item_id"],
        "receipt_order": a["order"],
        "submission_id": a["submission_id"],
        "result_kind": "generated",
        "timing_started_at": to_iso(clock(0.2)),
        "min_display_seconds": 10,
        "played": False,
    }
    assert result["pending"] == {
        "playback_item_id": b["item_id"],
        "receipt_order": b["order"],
        "ready_roles": ["visitor"],
    }
    assert result["clients"]["ai"] == {
        "connected": True,
        "shown_revision": 1,
        "last_heartbeat_at": to_iso(clock(0.3)),
    }


# ---------- WebSocket 協定（實際連線，最低展示 1 秒） ----------


@pytest.fixture
def pb_env(paths_env, monkeypatch):
    """預先建立資料庫與原圖；原圖同步由 backend-core 負責，這裡隔離掉。"""
    from backend.services import artworks

    monkeypatch.setattr(artworks, "sync_active_artwork", lambda conn: None)
    conn = db.connect()
    db.init_db(conn)
    with transaction(conn):
        settings.update(conn, {"display_min_seconds": 1})
    artwork = seed_artwork(conn)
    try:
        yield conn, artwork
    finally:
        conn.close()


@pytest.fixture
def ws_client(pb_env):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from contextlib import asynccontextmanager
    from backend.api import display, ws
    from backend.playback import controller

    # 保留 v1 協定作為舊資料／相容模組測試，不使用 v2 的正式應用生命週期。
    @asynccontextmanager
    async def legacy_lifespan(_app):
        await controller.start()
        try:
            yield
        finally:
            await controller.stop()

    app = FastAPI(lifespan=legacy_lifespan)
    app.include_router(display.router)
    app.include_router(ws.router)
    with TestClient(app) as test_client:
        yield test_client


def recv(ws, timeout: float = 5.0, skip: tuple[str, ...] = ("progress",)) -> dict:
    """接收下一則訊息（略過指定類型），逾時即失敗，避免測試卡住。"""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("等待 WebSocket 訊息逾時")

        async def _receive():
            with anyio.fail_after(remaining):
                return await ws._send_rx.receive()

        message = ws.portal.call(_receive)
        ws._raise_on_close(message)
        data = json.loads(message["text"])
        if data["type"] not in skip:
            return data


def recv_until(ws, kind: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        message = recv(ws, max(0.05, deadline - time.monotonic()), skip=())
        if message["type"] == kind:
            return message


def connect(client, role: str, client_id: str | None = None):
    return client.websocket_connect(f"/ws/events?role={role}&client_id={client_id or role}")


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("等待條件逾時")


def pending_revision(ws, hello: dict) -> int:
    """取得待切換版本：連線時已準備則在 hello 內，否則等 prepare。"""
    if hello["pending"] is not None:
        return hello["pending"]["revision"]
    return recv_until(ws, "prepare")["snapshot"]["revision"]


def test_ws_rejects_unknown_role(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/ws/events?role=tablet"):
            pass


def test_ws_hello_standby_and_role_replacement(ws_client, pb_env):
    conn, artwork = pb_env
    with connect(ws_client, "visitor", "first") as first:
        hello = recv(first)
        assert hello["type"] == "hello"
        assert hello["role"] == "visitor"
        assert hello["current"]["kind"] == "standby"
        assert hello["current"]["artist"]["id"] == artwork
        assert hello["pending"] is None
        assert set(hello["progress"]) == {"active", "started_at", "estimate_seconds", "queued_after"}
        assert "server_now" in hello

        with connect(ws_client, "visitor", "second") as second:
            assert recv(second)["type"] == "hello"
            with pytest.raises(WebSocketDisconnect) as closed:
                recv(first, skip=("progress", "prepare", "commit"))
            assert closed.value.code == 4000
            wait_until(
                lambda: tuple(
                    conn.execute(
                        "SELECT client_id, connected FROM display_clients WHERE role = 'visitor'"
                    ).fetchone()
                    or ()
                )
                == ("second", 1)
            )


def test_ws_full_cycle_prepare_commit_and_played(ws_client, pb_env):
    conn, artwork = pb_env
    a = add_item(conn, artwork)
    finish_generated(conn, a)

    with connect(ws_client, "visitor") as wv, connect(ws_client, "ai") as wa, connect(
        ws_client, "artist"
    ) as wr:
        sockets = (wv, wa, wr)
        revisions = {pending_revision(ws, recv(ws)) for ws in sockets}
        assert len(revisions) == 1
        revision = revisions.pop()
        for ws in sockets:
            ws.send_json({"type": "presented", "revision": 0})
            ws.send_json({"type": "ready", "revision": revision})

        commits = [recv_until(ws, "commit") for ws in sockets]
        assert {message["revision"] for message in commits} == {revision}
        assert all(message["switch_at"] for message in commits)

        current = ws_client.get("/api/display/current").json()
        assert current["current"]["revision"] == revision
        assert current["current"]["playback_item_id"] == a["item_id"]
        assert current["pending"] is None

        for ws in sockets:
            ws.send_json({"type": "presented", "revision": revision})
            ws.send_json({"type": "heartbeat", "shown_revision": revision})
        wait_until(lambda: item_row(conn, a)["status"] == "played")
        assert [run["result"] for run in runs_of(conn, a)] == ["completed"]
        wait_until(
            lambda: all(
                client["connected"] and client["shown_revision"] == revision
                for client in status.get_playback_status(conn)["clients"].values()
            )
        )

        b = add_item(conn, artwork)
        finish_failed(conn, b)
        prepares = [recv_until(ws, "prepare") for ws in sockets]
        assert {message["snapshot"]["revision"] for message in prepares} == {revision + 1}
        assert prepares[1]["snapshot"]["ai"] == {"type": "failure", "text": FAILURE_TEXT}
        for ws in sockets:
            ws.send_json({"type": "ready", "revision": revision + 1})
        assert {recv_until(ws, "commit")["revision"] for ws in sockets} == {revision + 1}


def test_ws_late_role_gets_pending_in_hello(ws_client, pb_env):
    conn, artwork = pb_env
    a = add_item(conn, artwork)
    finish_generated(conn, a)

    with connect(ws_client, "visitor") as wv, connect(ws_client, "artist") as wr:
        revision = pending_revision(wv, recv(wv))
        assert pending_revision(wr, recv(wr)) == revision
        for ws in (wv, wr):
            ws.send_json({"type": "ready", "revision": revision})
        time.sleep(0.6)
        assert item_row(conn, a)["status"] == "preparing"  # ai 未連線，不切換

        current = ws_client.get("/api/display/current").json()
        assert current["current"]["kind"] == "standby"
        assert current["pending"]["revision"] == revision

        with connect(ws_client, "ai") as wa:
            hello = recv(wa)
            assert hello["pending"]["revision"] == revision
            assert hello["pending"]["playback_item_id"] == a["item_id"]
            wa.send_json({"type": "ready", "revision": revision})
            assert {recv_until(ws, "commit")["revision"] for ws in (wv, wr, wa)} == {revision}


def test_ws_progress_broadcast(ws_client, pb_env):
    conn, artwork = pb_env
    with connect(ws_client, "ai") as wa:
        recv(wa)
        item = add_item(conn, artwork)
        add_item(conn, artwork)
        started_at = to_iso(now_utc())
        conn.execute(
            "UPDATE ai_jobs SET status = 'running', started_at = ? WHERE id = ?",
            (started_at, item["job_id"]),
        )
        deadline = time.monotonic() + 5
        while True:
            message = recv_until(wa, "progress", max(0.1, deadline - time.monotonic()))
            if message["progress"]["active"]:
                break
        assert message["progress"] == {
            "active": True,
            "started_at": started_at,
            "estimate_seconds": 60,
            "queued_after": 1,
        }


def test_ws_client_error_is_logged(ws_client, pb_env, paths_env):
    with connect(ws_client, "artist") as wr:
        recv(wr)
        wr.send_json({"type": "client_error", "message": "圖片解碼失敗"})
        wait_until(
            lambda: paths_env.runtime_log.exists()
            and "display.client_error" in paths_env.runtime_log.read_text(encoding="utf-8")
        )
