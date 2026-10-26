"""藝術家原圖版本管理。

直接置換專案內的 base.webp 即可更換原圖。每次檢查先比對檔案修改時間與大小，
有變動才計算雜湊；新內容可解碼時複製為 artworks/<id>/original.<副檔名> 並登錄新版本。
同一內容（雜湊相同）沿用既有版本；無法解碼時沿用前一版並記錄 WARNING。
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
from typing import Any

from PIL import Image

from backend.config import get_paths
from backend.db import transaction
from backend.logclient import get_logger
from backend.services import state
from backend.timeutil import now_iso

log = get_logger("web")


def media_url(relative_path: str, version: int | None = None) -> str:
    url = "/media/" + relative_path.replace("\\", "/")
    return f"{url}?v={version}" if version is not None else url


def _info_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "url": media_url(row["file_path"], row["id"]),
        "width": row["width"],
        "height": row["height"],
    }


def artwork_info(conn: sqlite3.Connection, artwork_id: int) -> dict[str, Any] | None:
    """ArtworkInfo：{id, url, width, height}。"""
    row = conn.execute("SELECT * FROM artworks WHERE id = ?", (artwork_id,)).fetchone()
    return _info_from_row(row) if row else None


def get_active_artwork(conn: sqlite3.Connection) -> dict[str, Any] | None:
    artwork_id = state.get(conn, "active_artwork_id")
    return artwork_info(conn, int(artwork_id)) if artwork_id is not None else None


def _source_stat() -> dict[str, int] | None:
    try:
        stat = get_paths().base_image.stat()
    except FileNotFoundError:
        return None
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def sync_active_artwork(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """偵測 base.webp 變動並回傳目前啟用的原圖資訊；不可在交易內呼叫。"""
    source_stat = _source_stat()
    if source_stat is None or (
        source_stat == state.get(conn, "base_image_stat")
        and state.get(conn, "active_artwork_id") is not None
    ):
        return get_active_artwork(conn)

    with transaction(conn):
        # 取得寫入鎖後再確認一次，避免多個請求重複登錄
        source_stat = _source_stat()
        if source_stat is None or source_stat == state.get(conn, "base_image_stat"):
            return get_active_artwork(conn)
        _register_source(conn, source_stat)
    return get_active_artwork(conn)


def _register_source(conn: sqlite3.Connection, source_stat: dict[str, int]) -> None:
    paths = get_paths()
    source = paths.base_image
    state.set(conn, "base_image_stat", source_stat)
    try:
        data = source.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
    except Exception as error:  # noqa: BLE001 - 任何解碼失敗都沿用前一版
        log.warning(
            "artwork.invalid",
            f"原圖無法解碼，沿用前一版：{type(error).__name__}: {error}",
            path=str(source),
        )
        return

    sha256 = hashlib.sha256(data).hexdigest()
    existing = conn.execute("SELECT id FROM artworks WHERE sha256 = ?", (sha256,)).fetchone()
    if existing is not None:
        artwork_id = int(existing["id"])
        if state.get(conn, "active_artwork_id") != artwork_id:
            state.set(conn, "active_artwork_id", artwork_id)
            log.info("artwork.registered", "原圖改回既有版本", artwork_id=artwork_id)
        return

    suffix = source.suffix.lower() or ".webp"
    cursor = conn.execute(
        "INSERT INTO artworks (sha256, width, height, file_path, created_at) VALUES (?, ?, ?, '', ?)",
        (sha256, width, height, now_iso()),
    )
    artwork_id = int(cursor.lastrowid)
    relative = f"artworks/{artwork_id}/original{suffix}"
    target = paths.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    conn.execute("UPDATE artworks SET file_path = ? WHERE id = ?", (relative, artwork_id))
    state.set(conn, "active_artwork_id", artwork_id)
    log.info(
        "artwork.registered",
        f"登錄新原圖版本 {width}x{height}",
        artwork_id=artwork_id,
        sha256=sha256[:12],
    )
