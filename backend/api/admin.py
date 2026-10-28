"""後臺 API /api/admin/*：總覽、佇列、設定、接待、日誌、螢幕、金鑰、試生成與啟動器要求。

來源 IP 由 AdminNetGuardMiddleware 限制，這裡不另外驗證身分。
"""

from __future__ import annotations

import io
import sqlite3
import uuid
from typing import Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel

from backend import db, exhibition
from backend.api.errors import ApiError
from backend.config import (
    DISPLAY_ROLES,
    get_paths,
    read_displays_config,
    read_openai_key,
    write_displays_config,
    write_openai_key,
)
from backend.db import transaction
from backend.logclient import get_logger
from backend.platform import monitors
from backend.playback import status as playback_status
from backend.services import artworks, health, runtime_log, sessions, settings, state
from backend.timeutil import now_iso
from backend.worker import ai_test

router = APIRouter(prefix="/api/admin")
log = get_logger("web")

_LAUNCHER_REQUESTS = ("shutdown", "relaunch_displays")
_AI_TEST_FIELDS = ("ai_model", "ai_quality", "ai_size", "ai_prompt")


class IntakeBody(BaseModel):
    intake_enabled: bool


class OpenAIKeyBody(BaseModel):
    api_key: str


class LauncherRequestBody(BaseModel):
    name: Literal["shutdown", "relaunch_displays"]


def _count(conn: sqlite3.Connection, sql: str, *params: Any) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _set_launcher_request(conn: sqlite3.Connection, name: str, value: bool) -> dict[str, bool]:
    requests = {key: False for key in _LAUNCHER_REQUESTS}
    requests.update(state.get(conn, "launcher_requests") or {})
    requests[name] = value
    state.set(conn, "launcher_requests", requests)
    return requests


@router.get("/overview")
def overview() -> dict[str, Any]:
    with db.connection() as conn:
        active = sessions.get_active_row(conn)
        version_id, _ = settings.get_current(conn)
        counts = {
            "ai_queued": _count(conn, "SELECT COUNT(*) FROM ai_jobs WHERE status = 'queued'"),
            "ai_running": _count(conn, "SELECT COUNT(*) FROM ai_jobs WHERE status = 'running'"),
            "playback_unplayed": _count(
                conn, "SELECT COUNT(*) FROM playback_items WHERE status != 'played'"
            ),
            "played_total": _count(
                conn, "SELECT COUNT(*) FROM playback_items WHERE status = 'played'"
            ),
            "submissions_total": _count(conn, "SELECT COUNT(*) FROM submissions"),
            "empty_total": _count(conn, "SELECT COUNT(*) FROM sessions WHERE status = 'empty'"),
            "discarded_total": _count(
                conn, "SELECT COUNT(*) FROM sessions WHERE status = 'discarded'"
            ),
            "abandoned_total": _count(
                conn, "SELECT COUNT(*) FROM sessions WHERE status = 'abandoned'"
            ),
        }
        return {
            "server_now": now_iso(),
            "intake_enabled": state.is_intake_enabled(conn),
            "health": health.get_health(conn),
            "active_session": sessions.session_view(conn, active) if active else None,
            "next_session_available_at": sessions.next_session_available_at(conn),
            "counts": counts,
            "playback": exhibition.payload(conn),
            "progress": playback_status.get_progress(conn),
            "settings_version_id": version_id,
        }


def _playback_runs(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT r.id, r.playback_item_id, p.receipt_order, r.timing_started_at, r.ended_at,
                   r.result, r.min_display_seconds
            FROM playback_runs r
            LEFT JOIN playback_items p ON p.id = r.playback_item_id
            ORDER BY r.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError as error:
        log.warning("admin.queues", f"讀取播放紀錄失敗：{error}")
        return []
    return [dict(row) for row in rows]


@router.get("/queues")
def queues(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    with db.connection() as conn:
        ai_jobs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT j.id, j.submission_id, j.receipt_order, j.status, j.started_at,
                       j.finished_at, a.duration_ms, a.model, j.error_code, j.error_message
                FROM ai_jobs j
                LEFT JOIN ai_attempts a ON a.id = j.current_attempt_id
                ORDER BY j.receipt_order DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        playback_items = []
        for row in conn.execute(
            """
            SELECT p.id, p.submission_id, p.receipt_order, p.status, p.result_kind, p.played_at,
                   p.content_version, s.dir_path, a.result_file
            FROM playback_items p
            JOIN submissions s ON s.id = p.submission_id
            LEFT JOIN ai_attempts a ON a.id = p.ai_attempt_id
            ORDER BY p.receipt_order DESC LIMIT ?
            """,
            (limit,),
        ).fetchall():
            ai_url = None
            if row["result_kind"] == "generated" and row["result_file"]:
                ai_url = artworks.media_url(row["result_file"], row["content_version"])
            playback_items.append(
                {
                    "id": row["id"],
                    "submission_id": row["submission_id"],
                    "receipt_order": row["receipt_order"],
                    "status": row["status"],
                    "result_kind": row["result_kind"],
                    "played_at": row["played_at"],
                    "visitor_url": artworks.media_url(f"{row['dir_path']}/visitor.png", 1),
                    "ai_url": ai_url,
                }
            )
        recent_sessions = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, status, started_at, ended_at, end_reason
                FROM sessions ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]
        return {
            "server_now": now_iso(),
            "ai_jobs": ai_jobs,
            "playback_items": playback_items,
            "playback_runs": [dict(row) for row in conn.execute(
                """SELECT r.id, r.playback_item_id, p.receipt_order,
                          r.started_at AS timing_started_at, r.ended_at, r.result,
                          r.seconds AS min_display_seconds, r.kind, r.stage
                   FROM exhibition_segments r JOIN playback_items p ON p.id = r.playback_item_id
                   ORDER BY r.id DESC LIMIT ?""", (limit,)
            )],
            "legacy_playback_runs": _playback_runs(conn, limit),
            "exhibition_segments": [dict(row) for row in conn.execute(
                "SELECT * FROM exhibition_segments ORDER BY id DESC LIMIT ?", (limit,)
            )],
            "sessions": recent_sessions,
        }


def _settings_response(conn: sqlite3.Connection) -> dict[str, Any]:
    version_id, current = settings.get_current(conn)
    return {
        "server_now": now_iso(),
        "version_id": version_id,
        "settings": current,
        "defaults": settings.DEFAULT_SETTINGS,
        "allowed_models": list(settings.ALLOWED_MODELS),
        "allowed_qualities": list(settings.ALLOWED_QUALITIES),
    }


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    with db.connection() as conn:
        return _settings_response(conn)


@router.patch("/settings")
def patch_settings(patch: dict[str, Any]) -> dict[str, Any]:
    with db.connection() as conn:
        _, before = settings.get_current(conn)
        try:
            with transaction(conn):
                version_id, after = settings.update(conn, patch, note="後臺修改")
        except settings.SettingsError as error:
            raise ApiError(
                422, "settings_invalid", f"設定不正確：{error.message}", {"field": error.field}
            ) from error
        changed = sorted(key for key in after if after[key] != before.get(key))
        if changed:
            log.info(
                "settings.updated",
                f"設定更新：{', '.join(changed)}",
                settings_version_id=version_id,
            )
        return _settings_response(conn)


@router.get("/intake")
def get_intake() -> dict[str, Any]:
    with db.connection() as conn:
        return {"server_now": now_iso(), "intake_enabled": state.is_intake_enabled(conn)}


@router.patch("/intake")
def patch_intake(body: IntakeBody) -> dict[str, Any]:
    with db.connection() as conn:
        with transaction(conn):
            changed = state.is_intake_enabled(conn) != body.intake_enabled
            state.set_intake_enabled(conn, body.intake_enabled)
        if changed:
            log.info(
                "settings.intake_changed",
                "恢復接待" if body.intake_enabled else "暫停接待",
            )
        return {"server_now": now_iso(), "intake_enabled": body.intake_enabled}


@router.get("/logs")
def get_logs(offset: int | None = None, max_bytes: int = runtime_log.DEFAULT_MAX_BYTES) -> dict[str, Any]:
    return {"server_now": now_iso(), **runtime_log.read_chunk(offset, max_bytes)}


@router.get("/logs/download")
def download_logs() -> Response:
    path = get_paths().runtime_log
    if not path.is_file():
        return Response(b"", media_type="text/plain; charset=utf-8")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename="runtime.txt")


def _displays_response(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "server_now": now_iso(),
        "config": read_displays_config(),
        "monitors": monitors.list_monitors(),
        "clients": health.get_health(conn)["checks"]["displays"]["roles"],
    }


@router.get("/displays")
def get_displays() -> dict[str, Any]:
    with db.connection() as conn:
        return _displays_response(conn)


@router.patch("/displays")
def patch_displays(patch: dict[str, Any]) -> dict[str, Any]:
    config = read_displays_config()
    for key, value in patch.items():
        if key in (*DISPLAY_ROLES, "exhibition"):
            monitor = value.get("monitor") if isinstance(value, dict) else None
            if isinstance(monitor, bool) or not isinstance(monitor, int) or monitor < 0:
                raise ApiError(
                    422, "settings_invalid", "螢幕編號必須是 0 以上的整數", {"field": key}
                )
            config[key] = {**config.get(key, {}), "monitor": monitor}
        elif key == "browser":
            if not isinstance(value, str) or not value.strip():
                raise ApiError(422, "settings_invalid", "瀏覽器設定不可為空白", {"field": key})
            config["browser"] = value.strip()
        else:
            raise ApiError(422, "settings_invalid", "未知的螢幕設定項目", {"field": key})
    write_displays_config(config)
    log.info(
        "settings.displays_changed",
        "螢幕配置更新："
        + ", ".join(f"{role}={config[role]['monitor']}" for role in DISPLAY_ROLES),
    )
    with db.connection() as conn:
        return _displays_response(conn)


@router.post("/displays/relaunch")
def relaunch_displays() -> dict[str, Any]:
    with db.connection() as conn:
        with transaction(conn):
            requests = _set_launcher_request(conn, "relaunch_displays", True)
    log.info("admin.relaunch_displays", "要求啟動器重新開啟展示視窗")
    return {"server_now": now_iso(), "launcher_requests": requests}


@router.post("/exhibition/stop")
def stop_exhibition(source: Literal["admin", "escape"] = "admin") -> dict[str, Any]:
    with db.connection() as conn:
        with transaction(conn):
            state.pause_intake_for_stop(conn)
            requests = _set_launcher_request(conn, "shutdown", True)
    message = "ESC 五秒內按五次，要求結束展覽" if source == "escape" else "後臺要求結束展覽，已暫停接待"
    log.info("admin.exhibition_stop", message, source=source)
    return {"server_now": now_iso(), "intake_enabled": False, "launcher_requests": requests}


@router.post("/launcher-requests/clear")
def clear_launcher_request(body: LauncherRequestBody) -> dict[str, Any]:
    with db.connection() as conn:
        with transaction(conn):
            requests = _set_launcher_request(conn, body.name, False)
    return {"server_now": now_iso(), "launcher_requests": requests}


def _openai_key_response() -> dict[str, Any]:
    _, source = read_openai_key()
    return {"server_now": now_iso(), "configured": source is not None, "source": source}


@router.get("/openai-key")
def get_openai_key() -> dict[str, Any]:
    return _openai_key_response()


@router.put("/openai-key")
def put_openai_key(body: OpenAIKeyBody) -> dict[str, Any]:
    api_key = body.api_key.strip()
    if not api_key:
        raise ApiError(422, "validation_error", "金鑰不可為空白")
    write_openai_key(api_key)
    log.info("settings.openai_key_set", "已更新 OpenAI 金鑰設定檔")
    return _openai_key_response()


@router.post("/ai-test")
def run_ai_test(
    image: UploadFile | None = File(None),
    ai_model: str | None = Form(None),
    ai_quality: str | None = Form(None),
    ai_size: str | None = Form(None),
    ai_prompt: str | None = Form(None),
) -> dict[str, Any]:
    provided = {"ai_model": ai_model, "ai_quality": ai_quality, "ai_size": ai_size, "ai_prompt": ai_prompt}
    overrides = {key: value for key, value in provided.items() if value not in (None, "")}

    with db.connection() as conn:
        _, current = settings.get_current(conn)
        try:
            merged = settings.validate({**current, **overrides})
        except settings.SettingsError as error:
            raise ApiError(
                422, "settings_invalid", f"設定不正確：{error.message}", {"field": error.field}
            ) from error
        latest = conn.execute(
            "SELECT dir_path FROM submissions ORDER BY receipt_order DESC LIMIT 1"
        ).fetchone()

    paths = get_paths()
    upload_path = None
    if image is not None and image.filename:
        data = image.file.read()
        try:
            with Image.open(io.BytesIO(data)) as decoded:
                decoded.load()
                upload_path = paths.tmp_dir / f"ai-test-upload-{uuid.uuid4().hex}.png"
                upload_path.parent.mkdir(parents=True, exist_ok=True)
                decoded.save(upload_path, format="PNG")
        except OSError as error:
            raise ApiError(422, "invalid_image", "無法解碼試生成圖片") from error
        image_path = upload_path
    elif latest is not None:
        image_path = paths.data_dir / latest["dir_path"] / "visitor.png"
    else:
        raise ApiError(409, "no_test_image", "沒有可用的試生成圖片，請上傳一張")

    # 試生成結果的日誌由 run_ai_test 記錄
    params = {key: merged[key] for key in (*_AI_TEST_FIELDS, "ai_timeout_seconds")}
    try:
        result = ai_test.run_ai_test(image_path, params)
    finally:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
    return {"server_now": now_iso(), **result}
