"""後臺付費試生成：以指定圖片與目前（或暫時覆寫的）設定生成一張，不進播放佇列。"""

from __future__ import annotations

import io
import json
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from backend import db
from backend.ai_providers import AIError, ImageRequest, get_provider
from backend.config import get_paths
from backend.logclient import get_logger
from backend.services import settings
from backend.timeutil import now_iso
from backend.worker.jobs import call_with_deadline, save_result

log = get_logger("web")

_OVERRIDE_KEYS = ("ai_model", "ai_quality", "ai_size", "ai_prompt")


def run_ai_test(image_path: Path, overrides: dict[str, Any]) -> dict[str, Any]:
    """同步執行一次試生成；結果保存於 ai-tests/<id>/。"""
    test_id = uuid.uuid4().hex
    patch = {key: overrides[key] for key in _OVERRIDE_KEYS if overrides.get(key) not in (None, "")}

    with db.connection() as conn:
        _, current = settings.get_current(conn)
    try:
        config = settings.validate({**current, **patch})
    except settings.SettingsError as error:
        return _result(test_id, "failed", {**current, **patch}, 0, None, "settings_invalid", error.message)

    relative_dir = f"ai-tests/{test_id}"
    directory = get_paths().data_dir / relative_dir
    input_path = directory / "input.png"
    started_at = now_iso()
    started = time.monotonic()
    request_id: str | None = None
    try:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with Image.open(image_path) as source:
                source.load()
                mode = "RGBA" if "A" in source.getbands() else "RGB"
                buffer = io.BytesIO()
                source.convert(mode).save(buffer, "PNG")
            input_path.write_bytes(buffer.getvalue())
        except OSError as error:
            raise AIError("invalid_image", f"試生成圖片無法讀取：{error}") from error

        request = ImageRequest(
            input_png=input_path,
            prompt=config["ai_prompt"],
            model=config["ai_model"],
            quality=config["ai_quality"],
            size=config["ai_size"],
            timeout_seconds=float(config["ai_timeout_seconds"]),
        )
        result = call_with_deadline(get_provider(), request)
        request_id = result.request_id
        saved = save_result(directory, relative_dir, result, "result.png")
    except AIError as error:
        duration_ms = int((time.monotonic() - started) * 1000)
        _write_metadata(directory, test_id, config, started_at, duration_ms, "failed", error.request_id, error)
        log.warning(
            "admin.ai_test",
            f"試生成失敗（{error.code}）：{error.message}",
            test_id=test_id,
            duration_ms=duration_ms,
        )
        return _result(test_id, "failed", config, duration_ms, None, error.code, error.message)

    duration_ms = int((time.monotonic() - started) * 1000)
    _write_metadata(directory, test_id, config, started_at, duration_ms, "succeeded", request_id, None)
    log.info(
        "admin.ai_test",
        f"試生成完成（{config['ai_model']}，{config['ai_quality']}，{config['ai_size']}）",
        test_id=test_id,
        duration_ms=duration_ms,
        request_id=request_id,
    )
    return _result(
        test_id, "succeeded", config, duration_ms, f"/media/{saved.result_file}?v=1", None, None
    )


def _result(
    test_id: str,
    status: str,
    config: dict[str, Any],
    duration_ms: int,
    result_url: str | None,
    error_code: str | None,
    error_message: str | None,
) -> dict[str, Any]:
    return {
        "id": test_id,
        "status": status,
        "model": config.get("ai_model"),
        "quality": config.get("ai_quality"),
        "size": config.get("ai_size"),
        "duration_ms": duration_ms,
        "result_url": result_url,
        "error_code": error_code,
        "error_message": error_message,
    }


def _write_metadata(
    directory: Path,
    test_id: str,
    config: dict[str, Any],
    started_at: str,
    duration_ms: int,
    status: str,
    request_id: str | None,
    error: AIError | None,
) -> None:
    data = {
        "id": test_id,
        "status": status,
        "model": config["ai_model"],
        "quality": config["ai_quality"],
        "size": config["ai_size"],
        "prompt": config["ai_prompt"],
        "timeout_seconds": config["ai_timeout_seconds"],
        "started_at": started_at,
        "finished_at": now_iso(),
        "duration_ms": duration_ms,
        "request_id": request_id,
        "error_code": error.code if error else None,
        "error_message": error.message if error else None,
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "metadata.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        log.warning("admin.ai_test", "無法寫入試生成資料", test_id=test_id)
