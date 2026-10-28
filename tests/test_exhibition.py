"""單螢幕：首播鎖、三段計時、歷史 K、中斷恢復與結束要求。"""

from __future__ import annotations

import io
import sqlite3
from datetime import timedelta

import pytest
from PIL import Image, ImageDraw

from backend import db, exhibition
from backend.ai_providers.fake import FakeImageProvider
from backend.api.errors import ApiError
from backend.services import artworks, sessions, settings, submissions
from backend.timeutil import now_utc
from backend.worker import jobs


def strokes() -> bytes:
    image = Image.new("RGBA", (640, 360))
    ImageDraw.Draw(image).line((20, 30, 500, 300), fill="red", width=10)
    data = io.BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def receive(conn):
    row = sessions.create_session(conn, "pen")
    result, view = submissions.submit(conn, row["id"], row["id"], "manual", strokes())
    assert result == "submitted"
    return view


def failed(conn):
    conn.execute("UPDATE ai_jobs SET status = 'failed' WHERE status IN ('queued', 'running')")
    conn.execute(
        """UPDATE playback_items SET status = 'ready', result_kind = 'failure_card',
                  content_version = content_version + 1 WHERE result_kind IS NULL"""
    )


def show_segment(conn, at):
    view = exhibition.load(conn)
    seconds = view["durations"][exhibition.STAGES.index(view["stage"])]
    exhibition.presented(conn, view["revision"], "test-screen", True, at)
    # 心跳要持續，不能以離線的十秒假裝完成展示。
    for elapsed in range(2, seconds + 1, 2):
        exhibition.presented(conn, view["revision"], "test-screen", True,
                             at + timedelta(seconds=elapsed))
    end = at + timedelta(seconds=seconds)
    exhibition.step(conn, end)
    return end + timedelta(milliseconds=1)


def complete_personal(conn):
    failed(conn)
    at = now_utc()
    for stage in exhibition.STAGES:
        assert exhibition.load(conn)["stage"] == stage
        at = show_segment(conn, at)
    assert exhibition.load(conn)["owner_submission_id"] is None


def test_slow_ai_keeps_own_picture_and_locks_every_stage(db_conn):
    received = receive(db_conn)
    owner = received["submission"]["id"]
    start = now_utc()
    view = exhibition.payload(db_conn)
    assert view["mode"] == "personal" and view["locked"] and not view["can_start"]
    assert owner in view["image_url"] and view["waiting_for_ai"]
    at = show_segment(db_conn, start)
    assert exhibition.load(db_conn)["stage"] == "visitor"
    with pytest.raises(ApiError, match="尚未完整展示"):
        sessions.create_session(db_conn, None)
    failed(db_conn)
    exhibition.step(db_conn, at)
    assert exhibition.payload(db_conn)["failure_text"] == "此刻，機器沉默。"
    assert exhibition.load(db_conn)["stage"] == "ai"
    with pytest.raises(ApiError):
        sessions.create_session(db_conn, None)
    at = show_segment(db_conn, at)
    assert exhibition.load(db_conn)["stage"] == "artist"
    assert not exhibition.payload(db_conn)["can_start"]
    show_segment(db_conn, at)
    assert exhibition.payload(db_conn)["can_start"]
    assert db_conn.execute("SELECT status FROM playback_items").fetchone()[0] == "played"
    assert db_conn.execute(
        "SELECT COUNT(*) FROM exhibition_segments WHERE kind = 'personal' AND result = 'completed'"
    ).fetchone()[0] == 3


def test_real_worker_success_cannot_skip_visitor_duration(db_conn):
    received = receive(db_conn)
    assert jobs.process_next_job(FakeImageProvider(delay_seconds=0, seed=3))
    start = now_utc()
    revision = exhibition.load(db_conn)["revision"]
    exhibition.presented(db_conn, revision, "test-screen", True, start)
    exhibition.step(db_conn, start + timedelta(seconds=2))
    assert exhibition.load(db_conn)["stage"] == "visitor"
    at = show_segment(db_conn, start)
    image = exhibition.payload(db_conn)
    assert image["stage"] == "ai" and "ai-result.png" in image["image_url"]
    assert received["submission"]["id"] in image["image_url"]
    at = show_segment(db_conn, at)
    show_segment(db_conn, at)
    assert exhibition.payload(db_conn)["can_start"]


def test_restart_and_hidden_screen_do_not_bypass_lock(db_conn):
    receive(db_conn)
    failed(db_conn)
    start = now_utc()
    view = exhibition.load(db_conn)
    exhibition.presented(db_conn, view["revision"], "test-screen", True, start)
    exhibition.presented(db_conn, view["revision"], "test-screen", False,
                         start + timedelta(seconds=3))
    assert exhibition.load(db_conn)["started_at"] is None
    # 過期版本不能讓新段落開始計時。
    exhibition.presented(db_conn, view["revision"], "test-screen", True,
                         start + timedelta(seconds=20))
    assert exhibition.load(db_conn)["started_at"] is None
    exhibition.recover(db_conn)
    restored = exhibition.load(db_conn)
    assert restored["mode"] == "personal" and restored["owner_submission_id"]
    assert restored["stage"] == "visitor" and restored["started_at"] is None
    with pytest.raises(ApiError):
        sessions.create_session(db_conn, None)
    complete_personal(db_conn)


def test_gallery_limit_does_not_delete_work_or_repeat_generation(db_conn):
    for _ in range(3):
        receive(db_conn)
        complete_personal(db_conn)
    with db.transaction(db_conn):
        settings.update(db_conn, {"gallery_limit": 2})
        view = exhibition.load(db_conn)
        view.update(item_id=None, kind=None, resume_item_id=None, started_at=None)
        exhibition.save(db_conn, view)
    assert exhibition._recent_ids(db_conn) == [2, 3]
    at = now_utc()
    exhibition.step(db_conn, at)
    assert exhibition.load(db_conn)["item_id"] == 2
    for _ in range(3):
        at = show_segment(db_conn, at)
    assert exhibition.load(db_conn)["item_id"] == 3
    for _ in range(3):
        at = show_segment(db_conn, at)
    assert exhibition.load(db_conn)["item_id"] == 2
    assert db_conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 3
    assert db_conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 3


def test_interrupted_history_returns_to_eligible_round(db_conn):
    for _ in range(2):
        receive(db_conn)
        complete_personal(db_conn)
    interrupted = exhibition.load(db_conn)["item_id"]
    receive(db_conn)
    assert exhibition.load(db_conn)["resume_item_id"] == interrupted
    complete_personal(db_conn)
    assert exhibition.load(db_conn)["item_id"] == interrupted
    assert exhibition.load(db_conn)["stage"] == "visitor"


def test_each_stage_binds_its_own_config_and_waits_for_presentation(db_conn):
    with db.transaction(db_conn):
        settings.update(db_conn, {"visitor_seconds": 2, "ai_seconds": 3, "artist_seconds": 4})
    receive(db_conn)
    failed(db_conn)
    view = exhibition.load(db_conn)
    assert view["durations"] == [2, 3, 4]
    with db.transaction(db_conn):
        settings.update(db_conn, {"visitor_seconds": 30})
    exhibition.step(db_conn, now_utc() + timedelta(days=1))
    assert exhibition.load(db_conn)["stage"] == "visitor"
    assert exhibition.load(db_conn)["durations"] == [2, 3, 4]


def test_blank_ends_drawing_and_retains_exception_cooldown(db_conn):
    row = sessions.create_session(db_conn, "pen")
    data = io.BytesIO()
    Image.new("RGBA", (640, 360)).save(data, "PNG")
    result, _ = submissions.submit(db_conn, row["id"], "blank", "timeout", data.getvalue())
    assert result == "empty"
    assert exhibition.load(db_conn)["mode"] == "gallery"
    assert not exhibition.payload(db_conn)["can_start"]
    assert not db_conn.execute("SELECT * FROM ai_jobs").fetchone()


def test_new_api_reports_local_state_and_escape_requests_shutdown(client, paths_env):
    initial = client.get("/api/exhibition").json()
    assert initial["mode"] == "gallery" and initial["can_start"]
    assert client.post("/api/exhibition/presented", json={
        "revision": initial["revision"], "client_id": "screen", "visible": True,
    }).status_code == 200
    assert client.get("/api/health").json()["checks"]["displays"]["roles"]["exhibition"]["connected"]
    response = client.post("/api/admin/exhibition/stop?source=escape").json()
    assert response["launcher_requests"]["shutdown"] and not response["intake_enabled"]
    assert "ESC 五秒內按五次" in paths_env.runtime_log.read_text(encoding="utf-8")


def test_v1_upgrade_backs_up_database_and_keeps_old_records(paths_env):
    conn = db.connect()
    for name in ("10_core.sql", "20_playback.sql"):
        conn.executescript((db.SCHEMA_DIR / name).read_text(encoding="utf-8"))
    with db.transaction(conn):
        settings.ensure_default_settings(conn)
    artworks.sync_active_artwork(conn)
    # 模擬已有作品的舊資料庫，不需對外 API 或真實展場資料。
    artwork_id = conn.execute("SELECT id FROM artworks").fetchone()[0]
    stamp = "2026-10-27T00:00:00.000Z"
    conn.execute(
        """INSERT INTO sessions (id, status, artwork_id, settings_version_id, started_at, deadline_at)
           VALUES ('old-session', 'submitted', ?, 1, ?, ?)""", (artwork_id, stamp, stamp)
    )
    conn.execute(
        """INSERT INTO submissions (id, session_id, receipt_order, submission_key, content_sha256,
           dir_path, width, height, received_at)
           VALUES ('old-work', 'old-session', 1, 'old-key', 'old-hash', 'submissions/old-work', 640, 360, ?)""",
        (stamp,),
    )
    conn.execute(
        """INSERT INTO playback_items (submission_id, receipt_order, status, created_at, updated_at)
           VALUES ('old-work', 1, 'played', ?, ?)""", (stamp, stamp)
    )
    folder = paths_env.submissions_dir / "old-work"
    folder.mkdir()
    image_bytes = strokes()
    (folder / "visitor.png").write_bytes(image_bytes)
    db.init_db(conn)
    assert conn.execute("SELECT id FROM submissions").fetchone()[0] == "old-work"
    assert conn.execute("SELECT status FROM playback_items").fetchone()[0] == "played"
    assert (folder / "visitor.png").read_bytes() == image_bytes
    backups = list((paths_env.data_dir / "backups").glob("before-v2-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
        assert not backup.execute(
            "SELECT name FROM sqlite_master WHERE name = 'exhibition_segments'"
        ).fetchone()
    db.init_db(conn)
    assert len(list((paths_env.data_dir / "backups").glob("*.sqlite3"))) == 1
    conn.close()


def test_fade_seconds_setting_reaches_exhibition_page(client):
    # 預設 1 秒；後臺修改後下一次輪詢即生效，可到小數一位，0 為直接切換
    assert client.get("/api/exhibition").json()["fade_seconds"] == 1.0
    body = client.patch("/api/admin/settings", json={"fade_seconds": 2.25}).json()
    assert body["settings"]["fade_seconds"] == 2.2
    assert client.get("/api/exhibition").json()["fade_seconds"] == 2.2
    assert client.patch("/api/admin/settings", json={"fade_seconds": 0}).json()["settings"]["fade_seconds"] == 0.0
    for value in (-1, 10.5, True, "1"):
        response = client.patch("/api/admin/settings", json={"fade_seconds": value})
        assert response.status_code == 422, value
        assert response.json()["error"]["details"]["field"] == "fade_seconds"
