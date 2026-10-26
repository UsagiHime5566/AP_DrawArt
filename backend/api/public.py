"""平板與公開 API：設定、場次、提交、作品查詢與健康檢查。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from backend import db
from backend.api.errors import ApiError
from backend.config import CANVAS_HEIGHT, CANVAS_WIDTH
from backend.services import artworks, health, sessions, settings, state, submissions
from backend.timeutil import now_iso

router = APIRouter()


class CreateSessionBody(BaseModel):
    tablet_id: str | None = None


class AbandonBody(BaseModel):
    reason: Literal["draft_lost", "staff"]


@router.get("/api/config")
def get_config() -> dict[str, Any]:
    with db.connection() as conn:
        artwork = artworks.sync_active_artwork(conn)
        if artwork is None:
            raise ApiError(503, "artwork_unavailable", "目前沒有可用的藝術家原圖")
        _, current = settings.get_current(conn)
        return {
            "server_now": now_iso(),
            "artwork": artwork,
            "settings": settings.public_subset(current),
            "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
            "intake_enabled": state.is_intake_enabled(conn),
        }


@router.get("/api/sessions/current")
def get_current_session() -> dict[str, Any]:
    with db.connection() as conn:
        return {"server_now": now_iso(), **sessions.current_state(conn)}


@router.post("/api/sessions", status_code=201)
def create_session(body: CreateSessionBody | None = None) -> dict[str, Any]:
    with db.connection() as conn:
        row = sessions.create_session(conn, body.tablet_id if body else None)
        return {"server_now": now_iso(), "session": sessions.session_view(conn, row)}


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = sessions.get_session_row(conn, session_id)
        return {"server_now": now_iso(), "session": sessions.session_view(conn, row)}


@router.post("/api/sessions/{session_id}/submit")
def submit_session(
    session_id: str,
    submission_key: str = Form(...),
    reason: str = Form(...),
    strokes: UploadFile = File(...),
) -> dict[str, Any]:
    data = strokes.file.read()
    with db.connection() as conn:
        result, view = submissions.submit(conn, session_id, submission_key, reason, data)
        return {"server_now": now_iso(), "result": result, "session": view}


@router.post("/api/sessions/{session_id}/abandon")
def abandon_session(session_id: str, body: AbandonBody) -> dict[str, Any]:
    with db.connection() as conn:
        row = sessions.abandon_session(conn, session_id, body.reason)
        return {"server_now": now_iso(), "session": sessions.session_view(conn, row)}


@router.get("/api/submissions/{submission_id}")
def get_submission(submission_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        return {"server_now": now_iso(), "submission": submissions.submission_view(conn, submission_id)}


@router.get("/api/health")
def get_health() -> dict[str, Any]:
    with db.connection() as conn:
        return {"server_now": now_iso(), **health.get_health(conn)}
