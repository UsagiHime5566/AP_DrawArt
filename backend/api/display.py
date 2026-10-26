"""GET /api/display/current：目前畫面、待切換畫面與生成進度（以資料庫為準）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend import db
from backend.playback import engine
from backend.timeutil import now_iso

router = APIRouter()


@router.get("/api/display/current")
def display_current() -> dict[str, Any]:
    with db.connection() as conn:
        payload = engine.current_payload(conn)
    return {"server_now": now_iso(), **payload}
