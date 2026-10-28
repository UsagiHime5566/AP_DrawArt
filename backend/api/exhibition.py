"""單螢幕的狀態查詢與實際呈現回報。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend import db, exhibition

router = APIRouter(prefix="/api/exhibition")


class PresentedBody(BaseModel):
    revision: int
    client_id: str = Field(min_length=1, max_length=64)
    visible: bool = True


@router.get("")
def current() -> dict[str, Any]:
    with db.connection() as conn:
        exhibition.step(conn)
        return exhibition.payload(conn)


@router.post("/presented")
def presented(body: PresentedBody) -> dict[str, Any]:
    with db.connection() as conn:
        exhibition.presented(conn, body.revision, body.client_id, body.visible)
        return {"ok": True}
