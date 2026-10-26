"""作品提交：空白判定、磁碟門檻、檔案落盤與收件交易。

一般收件流程：
1. 解碼上傳的透明筆跡，判定是否空白。
2. 在 data/tmp/<uuid>/ 寫好 strokes.png 與白底合成的 visitor.png（fsync）。
3. 取得資料庫寫入鎖後，寫 metadata.json、把暫存目錄搬到 submissions/<id>/，
   並在同一交易中結束場次、建立 submissions、ai_jobs 與 playback_items。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from backend.api.errors import ApiError
from backend.config import get_paths
from backend.db import transaction
from backend.logclient import get_logger
from backend.services import artworks, disk, sessions
from backend.timeutil import now_iso

log = get_logger("web")

SUBMIT_REASONS = ("manual", "timeout")
_RESULT_BY_STATUS = {"submitted": "submitted", "empty": "empty", "discarded": "discarded"}


class _AlreadyClosed(Exception):
    """交易內發現場次已結束（例如手動送出與到時送出同時抵達）。"""

    def __init__(self, row: sqlite3.Row) -> None:
        super().__init__(row["id"])
        self.row = row


def _closed_result(
    conn: sqlite3.Connection, row: sqlite3.Row, submission_key: str, content_sha256: str
) -> tuple[str, sqlite3.Row]:
    """場次已結束時的冪等判斷：同 key 同內容回原結果，否則回衝突。"""
    if row["submission_key"] == submission_key and row["status"] in _RESULT_BY_STATUS:
        if row["submission_sha256"] != content_sha256:
            log.warning(
                "submission.conflict",
                "同一提交識別碼的內容不同",
                session_id=row["id"],
            )
            raise ApiError(409, "submission_conflict", "提交識別碼相同但內容不同")
        log.info("session.resubmit", "重送提交，回覆原結果", session_id=row["id"])
        return _RESULT_BY_STATUS[row["status"]], row
    raise ApiError(
        409, "session_closed", "場次已結束", {"session": sessions.session_view(conn, row)}
    )


def _fsync_write(path: Path, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _decode(data: bytes) -> tuple[Image.Image, str | None]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            source_format = image.format
            return image.convert("RGBA"), source_format
    except Exception as error:  # noqa: BLE001 - 任何解碼失敗都視為無效圖片
        raise ApiError(422, "invalid_image", f"無法解碼筆跡圖片：{type(error).__name__}") from error


def _is_empty(image: Image.Image) -> bool:
    return image.getchannel("A").getextrema()[1] == 0


def _end_session(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    status: str,
    reason: str,
    submission_key: str,
    content_sha256: str,
) -> None:
    fields = sessions.end_fields(conn, row)
    conn.execute(
        """
        UPDATE sessions SET status = ?, ended_at = ?, end_reason = ?, next_session_available_at = ?,
                            submission_key = ?, submission_sha256 = ?
        WHERE id = ?
        """,
        (
            status,
            fields["ended_at"],
            reason,
            fields["next_session_available_at"],
            submission_key,
            content_sha256,
            row["id"],
        ),
    )


def _close_without_saving(
    conn: sqlite3.Connection,
    session_id: str,
    status: str,
    reason: str,
    submission_key: str,
    content_sha256: str,
) -> sqlite3.Row:
    with transaction(conn):
        row = sessions.get_session_row(conn, session_id)
        if row["status"] != "drawing":
            raise _AlreadyClosed(row)
        _end_session(conn, row, status, reason, submission_key, content_sha256)
    return sessions.get_session_row(conn, session_id)


def submit(
    conn: sqlite3.Connection,
    session_id: str,
    submission_key: str,
    reason: str,
    data: bytes,
) -> tuple[str, dict[str, Any]]:
    """處理提交，回傳 (result, SessionView)。"""
    submission_key = submission_key.strip()
    if not submission_key or len(submission_key) > 128:
        raise ApiError(422, "validation_error", "submission_key 格式錯誤")
    if reason not in SUBMIT_REASONS:
        raise ApiError(422, "validation_error", "reason 必須是 manual 或 timeout")

    content_sha256 = hashlib.sha256(data).hexdigest()
    row = sessions.get_session_row(conn, session_id)
    try:
        if row["status"] != "drawing":
            raise _AlreadyClosed(row)

        image, source_format = _decode(data)
        if _is_empty(image):
            row = _close_without_saving(
                conn, session_id, "empty", reason, submission_key, content_sha256
            )
            log.info("session.empty", "空白作品，結束場次且不送 AI", session_id=session_id)
            return "empty", sessions.session_view(conn, row)

        if disk.is_discarding():
            row = _close_without_saving(
                conn, session_id, "discarded", reason, submission_key, content_sha256
            )
            status = disk.disk_status()
            log.warning(
                "session.discarded",
                f"磁碟可用空間 {status['free_ratio']:.2%} 低於門檻，捨棄作品",
                session_id=session_id,
            )
            return "discarded", sessions.session_view(conn, row)

        row = _save_submission(
            conn, session_id, submission_key, reason, data, image, source_format, content_sha256
        )
        return "submitted", sessions.session_view(conn, row)
    except _AlreadyClosed as closed:
        result, row = _closed_result(conn, closed.row, submission_key, content_sha256)
        return result, sessions.session_view(conn, row)


def _save_submission(
    conn: sqlite3.Connection,
    session_id: str,
    submission_key: str,
    reason: str,
    data: bytes,
    image: Image.Image,
    source_format: str | None,
    content_sha256: str,
) -> sqlite3.Row:
    paths = get_paths()
    submission_id = uuid.uuid4().hex
    tmp_dir = paths.tmp_dir / submission_id
    final_relative = f"submissions/{submission_id}"
    final_dir = paths.data_dir / final_relative
    width, height = image.size

    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
        strokes = data if source_format == "PNG" else _png_bytes(image)
        _fsync_write(tmp_dir / "strokes.png", strokes)
        white = Image.new("RGBA", image.size, (255, 255, 255, 255))
        visitor = Image.alpha_composite(white, image).convert("RGB")
        _fsync_write(tmp_dir / "visitor.png", _png_bytes(visitor))
    except OSError as error:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.error(
            "submission.save_failed",
            f"作品暫存寫入失敗：{type(error).__name__}: {error}",
            session_id=session_id,
        )
        raise ApiError(500, "storage_error", "作品保存失敗，請重送") from error

    moved = False
    try:
        with transaction(conn):
            row = sessions.get_session_row(conn, session_id)
            if row["status"] != "drawing":
                raise _AlreadyClosed(row)

            receipt_order = int(
                conn.execute("SELECT COALESCE(MAX(receipt_order), 0) + 1 AS n FROM submissions")
                .fetchone()["n"]
            )
            received_at = now_iso()
            metadata = {
                "submission_id": submission_id,
                "session_id": session_id,
                "receipt_order": receipt_order,
                "received_at": received_at,
                "submission_key": submission_key,
                "reason": reason,
                "content_sha256": content_sha256,
                "artwork_id": row["artwork_id"],
                "settings_version_id": row["settings_version_id"],
                "width": width,
                "height": height,
                "files": {"strokes": "strokes.png", "visitor": "visitor.png"},
            }
            _fsync_write(
                tmp_dir / "metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_dir, final_dir)
            moved = True

            conn.execute(
                """
                INSERT INTO submissions (id, session_id, receipt_order, submission_key,
                                         content_sha256, dir_path, width, height, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    session_id,
                    receipt_order,
                    submission_key,
                    content_sha256,
                    final_relative,
                    width,
                    height,
                    received_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO ai_jobs (submission_id, receipt_order, settings_version_id, status,
                                     created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (submission_id, receipt_order, row["settings_version_id"], received_at, received_at),
            )
            conn.execute(
                """
                INSERT INTO playback_items (submission_id, receipt_order, status, created_at,
                                            updated_at)
                VALUES (?, ?, 'waiting_for_ai', ?, ?)
                """,
                (submission_id, receipt_order, received_at, received_at),
            )
            _end_session(conn, row, "submitted", reason, submission_key, content_sha256)
    except _AlreadyClosed:
        shutil.rmtree(final_dir if moved else tmp_dir, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as error:
        shutil.rmtree(final_dir if moved else tmp_dir, ignore_errors=True)
        log.error(
            "submission.save_failed",
            f"作品保存失敗：{type(error).__name__}: {error}",
            session_id=session_id,
            submission_id=submission_id,
        )
        raise ApiError(500, "storage_error", "作品保存失敗，請重送") from error

    if width * 9 != height * 16:
        log.warning(
            "submission.saved",
            f"作品尺寸 {width}x{height} 不是 16:9",
            session_id=session_id,
            submission_id=submission_id,
        )
    log.info(
        "submission.saved",
        f"作品已保存，收件順序 {receipt_order}",
        session_id=session_id,
        submission_id=submission_id,
        receipt_order=receipt_order,
    )
    log.info("session.submitted", f"場次送出（{reason}）", session_id=session_id)
    return sessions.get_session_row(conn, session_id)


def submission_view(conn: sqlite3.Connection, submission_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT s.*, j.status AS ai_status, p.status AS playback_status
        FROM submissions s
        LEFT JOIN ai_jobs j ON j.submission_id = s.id
        LEFT JOIN playback_items p ON p.submission_id = s.id
        WHERE s.id = ?
        """,
        (submission_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "submission_not_found", "找不到作品")
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "receipt_order": row["receipt_order"],
        "received_at": row["received_at"],
        "visitor_url": artworks.media_url(f"{row['dir_path']}/visitor.png", 1),
        "ai_status": row["ai_status"],
        "playback_status": row["playback_status"],
        "queue_ahead": sessions.queue_ahead(conn, row["receipt_order"]),
    }
