"""Web 程序：FastAPI 應用、API、WebSocket 與中央播放控制器。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend import db, exhibition
from backend.api import admin, display, exhibition as exhibition_api, pages, public
from backend.api.errors import install_error_handlers
from backend.api.netguard import AdminNetGuardMiddleware
from backend.config import get_paths, http_port
from backend.logclient import get_logger
from backend.services import artworks

log = get_logger("web")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_paths().ensure_dirs()
    with db.connection() as conn:
        db.init_db(conn)
        artworks.sync_active_artwork(conn)
    log.info("system.web_start", "Web 服務啟動", port=http_port())
    await exhibition.controller.start()
    try:
        yield
    finally:
        await exhibition.controller.stop()
        log.info("system.web_stop", "Web 服務結束")


def create_app() -> FastAPI:
    app = FastAPI(title="人工對畫(話) 展覽主機", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(AdminNetGuardMiddleware)
    app.include_router(public.router)
    app.include_router(admin.router)
    app.include_router(display.router)
    app.include_router(exhibition_api.router)
    app.include_router(pages.router)
    return app
