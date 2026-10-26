"""前端頁面與媒體檔案。

- /draw、/display/{role}、/admin 回傳 Vite 建置後的 HTML；/assets/* 為建置資源。
- /media/<相對 data_dir 的路徑> 提供原圖、觀眾作品與 AI 圖；只開放特定子目錄。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from backend.api.errors import ApiError
from backend.config import DISPLAY_ROLES, get_paths

router = APIRouter()

_MEDIA_ROOTS = ("artworks", "submissions", "ai-tests")


def _page(name: str) -> FileResponse:
    html = get_paths().static_dir / name / "index.html"
    if not html.is_file():
        raise ApiError(503, "frontend_missing", f"找不到前端建置檔：{name}/index.html")
    return FileResponse(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


def _safe_child(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        raise ApiError(404, "not_found", "找不到檔案")
    return target


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/draw")


@router.get("/draw", include_in_schema=False)
def draw_page() -> FileResponse:
    return _page("draw")


@router.get("/display/{role}", include_in_schema=False)
def display_page(role: str) -> FileResponse:
    if role not in DISPLAY_ROLES:
        raise ApiError(404, "not_found", "未知的展示角色")
    return _page("display")


@router.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return _page("admin")


@router.get("/assets/{path:path}", include_in_schema=False)
def assets(path: str) -> FileResponse:
    target = _safe_child(get_paths().static_dir / "assets", path)
    return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/media/{path:path}", include_in_schema=False)
def media(path: str) -> FileResponse:
    if path.split("/", 1)[0] not in _MEDIA_ROOTS:
        raise ApiError(404, "not_found", "找不到檔案")
    target = _safe_child(get_paths().data_dir, path)
    # 作品檔案不覆寫，網址另帶 ?v= 版本，可長期快取
    return FileResponse(target, headers={"Cache-Control": "public, max-age=86400"})
