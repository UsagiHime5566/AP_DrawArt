"""後臺 API、網段限制、日誌讀取與健康檢查。"""

from __future__ import annotations

import io
import ipaddress
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.timeutil import now_iso

FAKE_CLIENTS = {
    role: {"connected": True, "shown_revision": 0, "last_heartbeat_at": None}
    for role in ("exhibition",)
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """與播放控制器、螢幕列舉隔離（由其他工作包另行測試）。"""
    from backend.platform import monitors
    from backend.exhibition import controller
    from backend.playback import status as playback_status
    from backend.services import health

    async def _noop() -> None:
        return None

    monkeypatch.setattr(controller, "start", _noop)
    monkeypatch.setattr(controller, "stop", _noop)
    monkeypatch.setattr(health, "_check_displays", lambda _conn: {"ok": True, "roles": FAKE_CLIENTS})
    monkeypatch.setattr(
        playback_status,
        "get_playback_status",
        lambda _conn: {
            "phase": "idle",
            "display_revision": 0,
            "current": None,
            "pending": None,
            "clients": FAKE_CLIENTS,
        },
    )
    monkeypatch.setattr(
        playback_status,
        "get_progress",
        lambda _conn: {"active": False, "started_at": None, "estimate_seconds": 60, "queued_after": 0},
    )
    monkeypatch.setattr(
        monitors,
        "list_monitors",
        lambda: [
            {"index": 0, "name": "A", "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True}
        ],
    )


def png_bytes() -> bytes:
    image = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([(400, 300), (900, 800)], fill=(0, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def submit_one(client) -> dict:
    session = client.post("/api/sessions", json={}).json()["session"]
    response = client.post(
        f"/api/sessions/{session['id']}/submit",
        data={"submission_key": "k", "reason": "manual"},
        files={"strokes": ("strokes.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    return response.json()["session"]


def test_settings_validation_and_versions(client):
    body = client.get("/api/admin/settings").json()
    assert body["version_id"] == 1
    assert body["settings"]["ai_model"] == "gpt-image-2.5-sunburst"
    assert "gpt-image-2.5-flare" in body["allowed_models"]
    assert body["defaults"]["display_min_seconds"] == 10

    body = client.patch("/api/admin/settings", json={"display_min_seconds": 15}).json()
    assert body["version_id"] == 2 and body["settings"]["display_min_seconds"] == 15
    # 內容沒變不建立新版本
    assert client.patch("/api/admin/settings", json={"display_min_seconds": 15}).json()["version_id"] == 2

    for patch, field in (
        ({"ai_size": "1920x1080"}, "ai_size"),
        ({"ai_model": "dall-e-3"}, "ai_model"),
        ({"reference_opacity": 1.5}, "reference_opacity"),
        ({"no_such_field": 1}, "no_such_field"),
    ):
        response = client.patch("/api/admin/settings", json=patch)
        assert response.status_code == 422, patch
        assert response.json()["error"]["code"] == "settings_invalid"
        assert response.json()["error"]["details"]["field"] == field
    assert client.get("/api/admin/settings").json()["version_id"] == 2


def test_intake_toggle(client, paths_env):
    assert client.get("/api/admin/intake").json()["intake_enabled"] is True
    assert client.patch("/api/admin/intake", json={"intake_enabled": False}).json()["intake_enabled"] is False
    assert client.get("/api/config").json()["intake_enabled"] is False
    assert client.get("/api/sessions/current").json()["intake_enabled"] is False
    assert "settings.intake_changed" in paths_env.runtime_log.read_text(encoding="utf-8")


def test_logs_offsets(client, paths_env):
    log_path = paths_env.runtime_log
    lines = [f"line-{index:03d} 中文\n" for index in range(50)]
    log_path.write_bytes("".join(lines).encode("utf-8"))
    size = log_path.stat().st_size

    tail = client.get("/api/admin/logs", params={"max_bytes": 100}).json()
    assert tail["size"] == size and tail["next_offset"] == size
    assert tail["text"].endswith("line-049 中文\n")
    assert tail["text"].startswith("line-")  # 從完整行開始

    first = client.get("/api/admin/logs", params={"offset": 0, "max_bytes": 40}).json()
    assert first["offset"] == 0
    assert first["text"] == lines[0] + lines[1]
    following = client.get("/api/admin/logs", params={"offset": first["next_offset"], "max_bytes": 40}).json()
    assert following["text"].startswith(lines[2])

    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("partial line without newline")
    end = client.get("/api/admin/logs", params={"offset": size}).json()
    assert end["text"] == "" and end["next_offset"] == size

    beyond = client.get("/api/admin/logs", params={"offset": size * 10, "max_bytes": 100}).json()
    assert beyond["text"].endswith("line-049 中文\n")

    download = client.get("/api/admin/logs/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/plain")
    assert "line-000 中文" in download.content.decode("utf-8")


def test_admin_client_network_rules(monkeypatch):
    from backend.services import network

    monkeypatch.delenv("AP_DRAWART_ADMIN_ALLOW_ALL", raising=False)
    monkeypatch.setattr(
        network,
        "local_ipv4_interfaces",
        lambda: [ipaddress.IPv4Interface("192.168.50.28/24"), ipaddress.IPv4Interface("127.0.0.1/8")],
    )
    for host in ("192.168.50.77", "127.0.0.1", "::1", "::ffff:192.168.50.9"):
        assert network.is_admin_client_allowed(host), host
    for host in ("192.168.51.10", "10.0.0.5", "8.8.8.8", "fe80::1", "testclient", None, ""):
        assert not network.is_admin_client_allowed(host), host
    monkeypatch.setenv("AP_DRAWART_ADMIN_ALLOW_ALL", "1")
    assert network.is_admin_client_allowed("8.8.8.8")


def test_netguard_middleware(paths_env, monkeypatch):
    from backend.app import create_app

    monkeypatch.delenv("AP_DRAWART_ADMIN_ALLOW_ALL", raising=False)
    with TestClient(create_app(), client=("203.0.113.9", 5000)) as outsider:
        response = outsider.get("/api/admin/intake")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden_network"
        assert outsider.get("/admin").status_code == 403
        assert outsider.get("/api/config").status_code == 200
        assert outsider.get("/api/health").status_code == 200
    with TestClient(create_app(), client=("127.0.0.1", 5000)) as local:
        assert local.get("/api/admin/intake").status_code == 200


def test_health_and_openai_key(client, paths_env, monkeypatch):
    health = client.get("/api/health").json()
    assert set(health["checks"]) == {
        "database", "frontend", "artwork", "worker", "displays", "disk", "log", "openai_key",
    }
    assert health["status"] == "degraded"  # 沒有前端建置檔、沒有 Worker 心跳
    assert health["checks"]["frontend"]["missing"] == ["exhibition", "admin"]
    assert health["checks"]["worker"]["ok"] is False
    assert health["checks"]["openai_key"]["ok"] is True  # fake 供應商
    assert health["launcher_requests"] == {"shutdown": False, "relaunch_displays": False}
    assert all(url.endswith("/admin") for url in health["admin_urls"])

    for page in ("exhibition", "admin"):
        (paths_env.static_dir / page).mkdir(parents=True, exist_ok=True)
        (paths_env.static_dir / page / "index.html").write_text("<html></html>", encoding="utf-8")
    from backend import db
    from backend.services import state

    with db.connection() as conn:
        state.set(conn, "worker_heartbeat", {"pid": 42, "at": now_iso(), "job_id": None})
    # 單螢幕展覽頁回報實際顯示後才算已連線
    revision = client.get("/api/exhibition").json()["revision"]
    assert client.post(
        "/api/exhibition/presented", json={"revision": revision, "client_id": "screen", "visible": True}
    ).status_code == 200
    health = client.get("/api/health").json()
    assert health["status"] == "ok", health["checks"]
    assert health["checks"]["worker"]["pid"] == 42

    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "openai")
    assert client.get("/api/health").json()["status"] == "setup_required"
    key_status = client.get("/api/admin/openai-key").json()
    assert key_status["configured"] is False and key_status["source"] is None
    secret = "sk-test-0123456789abcdef"
    body = client.put("/api/admin/openai-key", json={"api_key": secret}).json()
    assert body["configured"] is True and body["source"] == "file"
    assert secret not in json.dumps(body)
    assert json.loads(paths_env.openai_key_file.read_text(encoding="utf-8"))["api_key"] == secret
    assert client.get("/api/health").json()["status"] == "ok"
    assert secret not in paths_env.runtime_log.read_text(encoding="utf-8")
    assert client.put("/api/admin/openai-key", json={"api_key": "  "}).status_code == 422


def test_displays_config(client, paths_env):
    body = client.get("/api/admin/displays").json()
    assert body["config"]["visitor"] == {"monitor": 0}
    assert body["monitors"][0]["width"] == 1920
    assert body["clients"]["exhibition"]["connected"] is True

    body = client.patch("/api/admin/displays", json={"ai": {"monitor": 2}, "artist": {"monitor": 1}}).json()
    assert body["config"]["ai"] == {"monitor": 2} and body["config"]["artist"] == {"monitor": 1}
    saved = json.loads(paths_env.displays_config.read_text(encoding="utf-8"))
    assert saved["ai"]["monitor"] == 2

    for patch in ({"ai": {"monitor": -1}}, {"ai": {"monitor": "x"}}, {"unknown": 1}, {"browser": ""}):
        assert client.patch("/api/admin/displays", json=patch).status_code == 422, patch


def test_launcher_requests(client):
    assert client.post("/api/admin/displays/relaunch").json()["launcher_requests"]["relaunch_displays"] is True
    assert client.get("/api/health").json()["launcher_requests"]["relaunch_displays"] is True
    body = client.post("/api/admin/launcher-requests/clear", json={"name": "relaunch_displays"}).json()
    assert body["launcher_requests"]["relaunch_displays"] is False

    body = client.post("/api/admin/exhibition/stop").json()
    assert body["intake_enabled"] is False and body["launcher_requests"]["shutdown"] is True
    assert client.get("/api/admin/intake").json()["intake_enabled"] is False
    assert client.post("/api/admin/launcher-requests/clear", json={"name": "bogus"}).status_code == 422


def test_overview_and_queues(client):
    view = submit_one(client)
    overview = client.get("/api/admin/overview").json()
    assert overview["counts"]["submissions_total"] == 1
    assert overview["counts"]["ai_queued"] == 1
    assert overview["counts"]["playback_unplayed"] == 1
    assert overview["active_session"] is None
    assert overview["next_session_available_at"] is None
    assert overview["playback"]["mode"] == "personal"
    assert overview["progress"]["active"] is False
    assert overview["settings_version_id"] == 1
    assert overview["health"]["status"] in ("ok", "degraded")

    queues = client.get("/api/admin/queues").json()
    assert queues["ai_jobs"][0]["status"] == "queued"
    item = queues["playback_items"][0]
    assert item["status"] == "waiting_for_ai" and item["ai_url"] is None
    assert item["visitor_url"].endswith(f"{view['submission']['id']}/visitor.png?v=1")
    assert queues["sessions"][0]["status"] == "submitted"
    assert queues["playback_runs"][0]["kind"] == "personal"


def test_ai_test_endpoint(client, monkeypatch):
    from backend.worker import ai_test

    calls = []

    def fake_run(image_path, overrides):
        calls.append((image_path, image_path.exists(), dict(overrides)))
        return {
            "id": "t1", "status": "succeeded", "model": overrides["ai_model"],
            "quality": overrides["ai_quality"], "size": overrides["ai_size"], "duration_ms": 5,
            "result_url": "/media/ai-tests/t1/result.png?v=1", "error_code": None, "error_message": None,
        }

    monkeypatch.setattr(ai_test, "run_ai_test", fake_run)

    response = client.post("/api/admin/ai-test")
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_test_image"

    response = client.post(
        "/api/admin/ai-test",
        data={"ai_model": "gpt-image-2.5-flare", "ai_quality": "low"},
        files={"image": ("x.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    path, existed, overrides = calls[-1]
    assert existed and not path.exists()  # 上傳的暫存檔在呼叫後刪除
    assert overrides["ai_model"] == "gpt-image-2.5-flare" and overrides["ai_quality"] == "low"
    assert overrides["ai_size"] == "2048x1152" and overrides["ai_timeout_seconds"] == 180

    view = submit_one(client)
    assert client.post("/api/admin/ai-test").status_code == 200
    assert calls[-1][0].parent.name == view["submission"]["id"]

    response = client.post("/api/admin/ai-test", data={"ai_size": "100x100"})
    assert response.status_code == 422
    response = client.post("/api/admin/ai-test", files={"image": ("x.png", b"junk", "image/png")})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_image"
