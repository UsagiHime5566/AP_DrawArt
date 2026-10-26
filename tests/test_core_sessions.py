"""場次、提交、冪等、放棄與 queue_ahead。"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image, ImageDraw

from backend import db
from backend.timeutil import parse_iso


@pytest.fixture(autouse=True)
def _isolate_playback(monkeypatch):
    """與播放控制器隔離（playback 工作包另行測試）。"""
    from backend.playback import controller

    async def _noop() -> None:
        return None

    monkeypatch.setattr(controller, "start", _noop)
    monkeypatch.setattr(controller, "stop", _noop)


def png_bytes(empty: bool = False, color=(255, 0, 0, 255)) -> bytes:
    image = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    if not empty:
        ImageDraw.Draw(image).line([(100, 100), (800, 600)], fill=color, width=12)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def start_session(client) -> dict:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["session"]


def submit(client, session_id: str, key: str = "key-1", data: bytes | None = None, reason="manual"):
    return client.post(
        f"/api/sessions/{session_id}/submit",
        data={"submission_key": key, "reason": reason},
        files={"strokes": ("strokes.png", data if data is not None else png_bytes(), "image/png")},
    )


def no_turnaround(client) -> None:
    assert client.patch("/api/admin/settings", json={"turnaround_seconds": 0}).status_code == 200


def test_config_and_create_session(client):
    config = client.get("/api/config").json()
    assert config["artwork"]["id"] == 1
    assert (config["artwork"]["width"], config["artwork"]["height"]) == (1600, 1132)
    assert config["canvas"] == {"width": 1920, "height": 1080}
    assert config["settings"] == {
        "drawing_duration_seconds": 90,
        "turnaround_seconds": 10,
        "reference_opacity": 0.3,
    }
    assert config["intake_enabled"] is True

    session = start_session(client)
    assert session["status"] == "drawing"
    assert session["artwork"]["id"] == 1
    duration = parse_iso(session["deadline_at"]) - parse_iso(session["started_at"])
    assert duration.total_seconds() == 90

    current = client.get("/api/sessions/current").json()
    assert current["active_session"]["id"] == session["id"]
    assert current["next_session_available_at"] is None
    assert client.get(f"/api/sessions/{session['id']}").json()["session"]["id"] == session["id"]


def test_create_session_conflicts(client):
    session = start_session(client)
    response = client.post("/api/sessions", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_active"
    assert response.json()["error"]["details"]["session_id"] == session["id"]

    assert submit(client, session["id"]).status_code == 200
    response = client.post("/api/sessions", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "turnaround"
    assert response.json()["error"]["details"]["next_session_available_at"]
    assert client.get("/api/sessions/current").json()["next_session_available_at"]

    with db.connection() as conn:
        conn.execute("UPDATE sessions SET next_session_available_at = '2000-01-01T00:00:00.000Z'")
    assert client.patch("/api/admin/intake", json={"intake_enabled": False}).status_code == 200
    response = client.post("/api/sessions", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "intake_paused"

    assert client.patch("/api/admin/intake", json={"intake_enabled": True}).status_code == 200
    assert client.post("/api/sessions", json={}).status_code == 201


def test_artwork_unavailable(client, paths_env):
    with db.connection() as conn:
        conn.execute("UPDATE runtime_state SET value_json = 'null' WHERE key = 'active_artwork_id'")
        conn.execute("UPDATE runtime_state SET value_json = 'null' WHERE key = 'base_image_stat'")
    paths_env.base_image.unlink()
    assert client.get("/api/config").json()["error"]["code"] == "artwork_unavailable"
    response = client.post("/api/sessions", json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "artwork_unavailable"


def test_submit_saves_files_and_queues(client, paths_env):
    session = start_session(client)
    response = submit(client, session["id"], reason="timeout")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "submitted"
    view = body["session"]
    assert view["status"] == "submitted"
    assert view["submission"]["queue_ahead"] == 0
    ended = parse_iso(view["ended_at"])
    assert (parse_iso(view["next_session_available_at"]) - ended).total_seconds() == 10

    submission_id = view["submission"]["id"]
    folder = paths_env.data_dir / "submissions" / submission_id
    assert (folder / "strokes.png").read_bytes() == png_bytes()
    with Image.open(folder / "visitor.png") as visitor:
        assert visitor.mode == "RGB"
        assert visitor.getpixel((0, 0)) == (255, 255, 255)
        assert visitor.getpixel((450, 350)) == (255, 0, 0)
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["receipt_order"] == 1 and metadata["reason"] == "timeout"
    assert not any(paths_env.tmp_dir.iterdir())

    with db.connection() as conn:
        job = conn.execute("SELECT * FROM ai_jobs").fetchone()
        item = conn.execute("SELECT * FROM playback_items").fetchone()
        row = conn.execute("SELECT * FROM sessions").fetchone()
    assert job["status"] == "queued" and job["submission_id"] == submission_id
    assert job["settings_version_id"] == 1
    assert item["status"] == "waiting_for_ai" and item["receipt_order"] == 1
    assert row["end_reason"] == "timeout"

    detail = client.get(f"/api/submissions/{submission_id}").json()["submission"]
    assert detail["ai_status"] == "queued"
    assert detail["playback_status"] == "waiting_for_ai"
    assert detail["visitor_url"] == f"/media/submissions/{submission_id}/visitor.png?v=1"
    assert client.get(detail["visitor_url"]).status_code == 200


def test_submit_empty_creates_nothing(client, paths_env):
    session = start_session(client)
    body = submit(client, session["id"], data=png_bytes(empty=True)).json()
    assert body["result"] == "empty"
    assert body["session"]["status"] == "empty"
    assert body["session"]["next_session_available_at"]
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 0
    assert not any((paths_env.data_dir / "submissions").iterdir())


def test_submit_discarded_when_disk_low(client, paths_env, monkeypatch):
    from backend.services import disk

    monkeypatch.setattr(disk, "_usage", lambda _path: (1000, 990, 10))
    session = start_session(client)
    body = submit(client, session["id"]).json()
    assert body["result"] == "discarded"
    assert body["session"]["status"] == "discarded"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0
    assert not any((paths_env.data_dir / "submissions").iterdir())
    log_text = paths_env.runtime_log.read_text(encoding="utf-8")
    assert "[WARNING]" in log_text and "session.discarded" in log_text

    # 重送同一份資料回原結果
    assert submit(client, session["id"]).json()["result"] == "discarded"
    assert client.get("/api/health").json()["checks"]["disk"]["discarding"] is True


def test_submit_idempotent_and_conflicts(client):
    session = start_session(client)
    first = submit(client, session["id"], key="same").json()
    second = submit(client, session["id"], key="same", reason="timeout").json()
    assert first["result"] == second["result"] == "submitted"
    assert first["session"]["submission"]["id"] == second["session"]["submission"]["id"]
    assert first["session"]["next_session_available_at"] == second["session"]["next_session_available_at"]
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM playback_items").fetchone()[0] == 1

    response = submit(client, session["id"], key="same", data=png_bytes(color=(0, 0, 255, 255)))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "submission_conflict"

    response = submit(client, session["id"], key="other")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_closed"
    assert response.json()["error"]["details"]["session"]["status"] == "submitted"


def test_submit_after_deadline_is_accepted(client):
    session = start_session(client)
    with db.connection() as conn:
        conn.execute(
            "UPDATE sessions SET deadline_at = '2000-01-01T00:00:00.000Z' WHERE id = ?",
            (session["id"],),
        )
    assert submit(client, session["id"], reason="timeout").json()["result"] == "submitted"


def test_invalid_image_and_unknown_session(client):
    session = start_session(client)
    response = submit(client, session["id"], data=b"not a png")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"
    assert client.get(f"/api/sessions/{session['id']}").json()["session"]["status"] == "drawing"

    assert submit(client, session["id"], reason="bogus").status_code == 422
    assert client.get("/api/sessions/does-not-exist").status_code == 404
    assert submit(client, "does-not-exist").json()["error"]["code"] == "session_not_found"


def test_abandon(client):
    no_turnaround(client)
    session = start_session(client)
    response = client.post(f"/api/sessions/{session['id']}/abandon", json={"reason": "draft_lost"})
    assert response.status_code == 200
    view = response.json()["session"]
    assert view["status"] == "abandoned" and view["next_session_available_at"]

    again = client.post(f"/api/sessions/{session['id']}/abandon", json={"reason": "staff"})
    assert again.status_code == 200 and again.json()["session"]["status"] == "abandoned"

    response = submit(client, session["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_closed"

    assert client.post(f"/api/sessions/{session['id']}/abandon", json={"reason": "x"}).status_code == 422

    second = start_session(client)
    assert submit(client, second["id"]).status_code == 200
    response = client.post(f"/api/sessions/{second['id']}/abandon", json={"reason": "staff"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_closed"


def test_queue_ahead(client):
    no_turnaround(client)
    submissions = []
    for index in range(3):
        session = start_session(client)
        body = submit(client, session["id"], key=f"k{index}").json()
        submissions.append(body["session"])
    assert [view["submission"]["queue_ahead"] for view in submissions] == [0, 1, 2]

    with db.connection() as conn:
        conn.execute("UPDATE playback_items SET status = 'played' WHERE receipt_order = 1")
    third = submissions[2]
    view = client.get(f"/api/sessions/{third['id']}").json()["session"]
    assert view["submission"]["queue_ahead"] == 1
    detail = client.get(f"/api/submissions/{third['submission']['id']}").json()["submission"]
    assert detail["receipt_order"] == 3 and detail["queue_ahead"] == 1


def test_settings_are_bound_to_session(client):
    assert client.patch("/api/admin/settings", json={"drawing_duration_seconds": 60}).status_code == 200
    session = start_session(client)
    assert session["settings"]["drawing_duration_seconds"] == 60
    assert client.patch("/api/admin/settings", json={"drawing_duration_seconds": 120}).status_code == 200
    view = client.get(f"/api/sessions/{session['id']}").json()["session"]
    assert view["settings"]["drawing_duration_seconds"] == 60
    duration = parse_iso(view["deadline_at"]) - parse_iso(view["started_at"])
    assert duration.total_seconds() == 60
