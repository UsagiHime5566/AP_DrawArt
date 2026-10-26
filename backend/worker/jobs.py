"""AI 生成工作：領取、呼叫供應商、保存結果或形成失敗展示。

規則（docs/implementation-contract.md 第 10 節）：
- 依 receipt_order 一次處理一件；每件只呼叫一次供應商，任何錯誤或逾時直接 failed。
- 成功與失敗都在單一交易內更新 ai_attempts、ai_jobs 與 playback_items。
- 寫入結果前確認該嘗試仍是工作的目前嘗試且仍在 running，遲到的結果一律捨棄。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from backend import db
from backend.ai_providers import AIError, ImageProvider, ImageRequest, ImageResult, get_provider
from backend.config import FAILURE_TEXT, get_paths
from backend.logclient import get_logger
from backend.services import settings, state
from backend.timeutil import now_iso

log = get_logger("worker")

_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    attempt_id: str | None
    submission_id: str
    receipt_order: int
    settings_version_id: int
    submission_dir: str
    attempt_dir: str | None
    model: str
    quality: str
    size: str
    prompt: str
    timeout_seconds: int
    started_at: str


@dataclass(frozen=True)
class SavedResult:
    result_file: str
    output_file: str | None
    width: int
    height: int


def worker_owner() -> str:
    return f"worker-{os.getpid()}"


def write_heartbeat(conn: Any, job_id: int | None) -> None:
    now = now_iso()
    state.set(conn, "worker_heartbeat", {"pid": os.getpid(), "at": now, "job_id": job_id})
    if job_id is not None:
        conn.execute(
            "UPDATE ai_jobs SET lease_heartbeat_at = ? WHERE id = ? AND status = 'running'",
            (now, job_id),
        )


def claim_next_job(owner: str | None = None) -> ClaimedJob | None:
    """領取收件順序最前的 queued 工作，建立嘗試並標記 running。"""
    with db.connection() as conn, db.transaction(conn):
        row = conn.execute(
            """
            SELECT j.id, j.submission_id, j.receipt_order, j.settings_version_id, s.dir_path
            FROM ai_jobs AS j JOIN submissions AS s ON s.id = j.submission_id
            WHERE j.status = 'queued'
            ORDER BY j.receipt_order
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        config = settings.get_version(conn, int(row["settings_version_id"]))
        attempt_id = uuid.uuid4().hex
        attempt_dir = f"{row['dir_path']}/ai/{attempt_id}"
        now = now_iso()
        conn.execute(
            """
            INSERT INTO ai_attempts (id, ai_job_id, status, model, quality, size, prompt,
                                     timeout_seconds, dir_path, started_at)
            VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                row["id"],
                config["ai_model"],
                config["ai_quality"],
                config["ai_size"],
                config["ai_prompt"],
                int(config["ai_timeout_seconds"]),
                attempt_dir,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE ai_jobs
            SET status = 'running', current_attempt_id = ?, lease_owner = ?,
                lease_heartbeat_at = ?, started_at = ?, finished_at = NULL,
                error_code = NULL, error_message = NULL, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (attempt_id, owner or worker_owner(), now, now, now, row["id"]),
        )
        return ClaimedJob(
            job_id=int(row["id"]),
            attempt_id=attempt_id,
            submission_id=row["submission_id"],
            receipt_order=int(row["receipt_order"]),
            settings_version_id=int(row["settings_version_id"]),
            submission_dir=row["dir_path"],
            attempt_dir=attempt_dir,
            model=config["ai_model"],
            quality=config["ai_quality"],
            size=config["ai_size"],
            prompt=config["ai_prompt"],
            timeout_seconds=int(config["ai_timeout_seconds"]),
            started_at=now,
        )


def call_with_deadline(provider: ImageProvider, request: ImageRequest) -> ImageResult:
    """在背景執行緒呼叫供應商；超過期限就放棄等待，之後抵達的結果不再使用。"""
    box: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["result"] = provider.generate(request)
        except BaseException as error:  # noqa: BLE001 - 交回呼叫端處理
            box["error"] = error
        finally:
            done.set()

    thread = threading.Thread(target=target, name="ai-provider-call", daemon=True)
    thread.start()
    if not done.wait(request.timeout_seconds):
        raise AIError("timeout", f"超過 {request.timeout_seconds:g} 秒未完成")
    error = box.get("error")
    if isinstance(error, AIError):
        raise error
    if error is not None:
        raise AIError("internal", f"{type(error).__name__}: {error}") from error
    return box["result"]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def save_result(directory: Path, relative_dir: str, result: ImageResult, result_name: str) -> SavedResult:
    """保存展示用 PNG（保持模型輸出尺寸）；非 PNG 輸出另存原始檔。"""
    try:
        image = Image.open(io.BytesIO(result.image_bytes))
        image.load()
    except Exception as error:  # noqa: BLE001 - 任何解碼失敗都視為回應錯誤
        raise AIError("bad_response", f"AI 回傳的圖片無法解碼：{type(error).__name__}") from error

    output_file: str | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        result_path = directory / result_name
        if image.format == "PNG":
            _atomic_write(result_path, result.image_bytes)
        else:
            ext = _MIME_EXT.get(result.mime_type) or (image.format or "bin").lower()
            output_name = f"ai-output.{ext}"
            _atomic_write(directory / output_name, result.image_bytes)
            output_file = f"{relative_dir}/{output_name}"
            mode = "RGBA" if "A" in image.getbands() else "RGB"
            buffer = io.BytesIO()
            image.convert(mode).save(buffer, "PNG")
            _atomic_write(result_path, buffer.getvalue())
        with Image.open(result_path) as check:
            check.verify()
    except OSError as error:
        raise AIError("storage_error", f"無法保存 AI 圖片：{error}") from error
    return SavedResult(f"{relative_dir}/{result_name}", output_file, image.width, image.height)


def _write_metadata(job: ClaimedJob, payload: dict[str, Any]) -> None:
    if job.attempt_dir is None:
        return
    path = get_paths().data_dir / job.attempt_dir / "metadata.json"
    data = {
        "attempt_id": job.attempt_id,
        "ai_job_id": job.job_id,
        "submission_id": job.submission_id,
        "receipt_order": job.receipt_order,
        "settings_version_id": job.settings_version_id,
        "model": job.model,
        "quality": job.quality,
        "size": job.size,
        "prompt": job.prompt,
        "timeout_seconds": job.timeout_seconds,
        "started_at": job.started_at,
        **payload,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    except OSError as error:
        log.warning(
            "ai.metadata_failed",
            f"無法寫入嘗試資料：{error}",
            ai_job_id=job.job_id,
            attempt_id=job.attempt_id,
        )


def _attempt_is_current(conn: Any, job: ClaimedJob) -> bool:
    row = conn.execute(
        """
        SELECT j.status, j.current_attempt_id, a.status AS attempt_status
        FROM ai_jobs AS j LEFT JOIN ai_attempts AS a ON a.id = j.current_attempt_id
        WHERE j.id = ?
        """,
        (job.job_id,),
    ).fetchone()
    if row is None or row["status"] != "running":
        return False
    if job.attempt_id is None:
        return row["current_attempt_id"] is None
    return row["current_attempt_id"] == job.attempt_id and row["attempt_status"] == "running"


def finish_success(
    job: ClaimedJob,
    saved: SavedResult,
    request_id: str | None,
    usage: dict[str, Any] | None,
    duration_ms: int | None,
) -> bool:
    """寫入成功結果；嘗試已不是目前嘗試時捨棄並回傳 False。"""
    now = now_iso()
    with db.connection() as conn, db.transaction(conn):
        if not _attempt_is_current(conn, job):
            current = False
        else:
            current = True
            conn.execute(
                """
                UPDATE ai_attempts
                SET status = 'succeeded', result_file = ?, request_id = ?, usage_json = ?,
                    finished_at = ?, duration_ms = ?
                WHERE id = ?
                """,
                (
                    saved.result_file,
                    request_id,
                    json.dumps(usage, ensure_ascii=False) if usage is not None else None,
                    now,
                    duration_ms,
                    job.attempt_id,
                ),
            )
            conn.execute(
                """
                UPDATE ai_jobs
                SET status = 'succeeded', finished_at = ?, lease_owner = NULL,
                    error_code = NULL, error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, job.job_id),
            )
            conn.execute(
                """
                UPDATE playback_items
                SET status = 'ready', result_kind = 'generated', ai_attempt_id = ?,
                    failure_text = NULL, content_version = content_version + 1, updated_at = ?
                WHERE submission_id = ? AND status = 'waiting_for_ai'
                """,
                (job.attempt_id, now, job.submission_id),
            )
    if not current:
        log.warning(
            "ai.late_result",
            "嘗試已不是目前嘗試，捨棄生成結果",
            ai_job_id=job.job_id,
            attempt_id=job.attempt_id,
        )
        return False
    _write_metadata(
        job,
        {
            "status": "succeeded",
            "finished_at": now,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "usage": usage,
            "result_file": saved.result_file,
            "output_file": saved.output_file,
            "result_width": saved.width,
            "result_height": saved.height,
        },
    )
    return True


def finish_failure(
    job: ClaimedJob,
    code: str,
    message: str,
    request_id: str | None,
    duration_ms: int | None,
) -> bool:
    """標記失敗並把待播項改為黑底失敗展示版；嘗試已不是目前嘗試時不動作。"""
    now = now_iso()
    with db.connection() as conn, db.transaction(conn):
        if not _attempt_is_current(conn, job):
            current = False
        else:
            current = True
            if job.attempt_id is not None:
                conn.execute(
                    """
                    UPDATE ai_attempts
                    SET status = 'failed', request_id = ?, error_code = ?, error_message = ?,
                        finished_at = ?, duration_ms = ?
                    WHERE id = ?
                    """,
                    (request_id, code, message, now, duration_ms, job.attempt_id),
                )
            conn.execute(
                """
                UPDATE ai_jobs
                SET status = 'failed', finished_at = ?, lease_owner = NULL,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, code, message, now, job.job_id),
            )
            conn.execute(
                """
                UPDATE playback_items
                SET status = 'ready', result_kind = 'failure_card', ai_attempt_id = NULL,
                    failure_text = ?, content_version = content_version + 1, updated_at = ?
                WHERE submission_id = ? AND status = 'waiting_for_ai'
                """,
                (FAILURE_TEXT, now, job.submission_id),
            )
    if not current:
        return False
    _write_metadata(
        job,
        {
            "status": "failed",
            "finished_at": now,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "error_code": code,
            "error_message": message,
            "failure_text": FAILURE_TEXT,
        },
    )
    return True


def process_job(job: ClaimedJob, provider: ImageProvider | None = None) -> str:
    """處理已領取的工作，回傳 succeeded / failed / discarded。"""
    paths = get_paths()
    ids = {
        "ai_job_id": job.job_id,
        "attempt_id": job.attempt_id,
        "submission_id": job.submission_id,
        "receipt_order": job.receipt_order,
    }
    assert job.attempt_dir is not None
    attempt_dir = paths.data_dir / job.attempt_dir
    input_path = attempt_dir / "ai-input.png"
    log.info("ai.start", f"開始生成（{job.model}，{job.quality}，{job.size}）", **ids)
    started = time.monotonic()

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        visitor = paths.data_dir / job.submission_dir / "visitor.png"
        if not visitor.is_file():
            raise AIError("input_missing", "找不到觀眾作品 visitor.png")
        try:
            attempt_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(visitor, input_path)
        except OSError as error:
            raise AIError("storage_error", f"無法建立 AI 輸入圖：{error}") from error

        active_provider = provider if provider is not None else get_provider()
        request = ImageRequest(
            input_png=input_path,
            prompt=job.prompt,
            model=job.model,
            quality=job.quality,
            size=job.size,
            timeout_seconds=float(job.timeout_seconds),
        )
        result = call_with_deadline(active_provider, request)
        saved = save_result(attempt_dir, job.attempt_dir, result, "ai-result.png")
    except AIError as error:
        duration = elapsed_ms()
        if finish_failure(job, error.code, error.message, error.request_id, duration):
            log.warning(
                "ai.failed",
                f"生成失敗（{error.code}）：{error.message}",
                duration_ms=duration,
                request_id=error.request_id,
                **ids,
            )
        return "failed"
    except Exception as error:  # noqa: BLE001 - 未預期錯誤也只形成失敗展示
        duration = elapsed_ms()
        log.exception("ai.failed", f"生成時發生未預期錯誤：{type(error).__name__}", **ids)
        finish_failure(job, "internal", f"{type(error).__name__}: {error}", None, duration)
        return "failed"

    duration = elapsed_ms()
    if not finish_success(job, saved, result.request_id, result.usage, duration):
        return "discarded"
    log.info(
        "ai.succeeded",
        f"生成完成，保存於 {saved.result_file}",
        duration_ms=duration,
        request_id=result.request_id,
        **ids,
    )
    return "succeeded"


def process_next_job(
    provider: ImageProvider | None = None,
    *,
    owner: str | None = None,
    on_claim: Callable[[int | None], None] | None = None,
) -> bool:
    """領取並處理一件工作；沒有待處理工作時回傳 False。"""
    job = claim_next_job(owner)
    if job is None:
        return False
    if on_claim is not None:
        on_claim(job.job_id)
    try:
        process_job(job, provider)
    finally:
        if on_claim is not None:
            on_claim(None)
    return True


def recover_interrupted_jobs() -> int:
    """啟動時處理上次中斷的 running 工作：已有完整結果者歸檔，其餘直接失敗，不重送雲端。"""
    paths = get_paths()
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT j.id, j.submission_id, j.receipt_order, j.settings_version_id,
                   j.current_attempt_id, j.started_at, s.dir_path AS submission_dir,
                   a.dir_path AS attempt_dir, a.model, a.quality, a.size, a.prompt,
                   a.timeout_seconds, a.started_at AS attempt_started_at
            FROM ai_jobs AS j
            JOIN submissions AS s ON s.id = j.submission_id
            LEFT JOIN ai_attempts AS a ON a.id = j.current_attempt_id
            WHERE j.status = 'running'
            ORDER BY j.receipt_order
            """
        ).fetchall()

    for row in rows:
        job = ClaimedJob(
            job_id=int(row["id"]),
            attempt_id=row["current_attempt_id"] if row["attempt_dir"] else None,
            submission_id=row["submission_id"],
            receipt_order=int(row["receipt_order"]),
            settings_version_id=int(row["settings_version_id"]),
            submission_dir=row["submission_dir"],
            attempt_dir=row["attempt_dir"],
            model=row["model"] or "",
            quality=row["quality"] or "",
            size=row["size"] or "",
            prompt=row["prompt"] or "",
            timeout_seconds=int(row["timeout_seconds"] or 0),
            started_at=row["attempt_started_at"] or row["started_at"] or now_iso(),
        )
        ids = {
            "ai_job_id": job.job_id,
            "attempt_id": job.attempt_id,
            "submission_id": job.submission_id,
            "receipt_order": job.receipt_order,
        }
        saved = _existing_result(paths.data_dir, job.attempt_dir)
        if saved is not None and finish_success(job, saved, None, None, None):
            log.info("ai.recovered", "Worker 重啟：已有完整生成結果，完成歸檔", **ids)
        elif finish_failure(job, "interrupted", "Worker 中斷，無法確認生成結果", None, None):
            log.warning("ai.recovered", "Worker 重啟：中斷的工作無完整結果，以失敗展示處理", **ids)
    return len(rows)


def _existing_result(data_dir: Path, attempt_dir: str | None) -> SavedResult | None:
    if attempt_dir is None:
        return None
    path = data_dir / attempt_dir / "ai-result.png"
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except Exception:  # noqa: BLE001 - 檔案不存在或不完整都視為沒有結果
        return None
    return SavedResult(f"{attempt_dir}/ai-result.png", None, width, height)
