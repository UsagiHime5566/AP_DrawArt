"""後臺設定的版本管理。

每次修改都建立新版本；場次綁定建立當時的版本，最低展示秒數在開始播放時讀取目前版本。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from backend.services import state
from backend.timeutil import now_iso

DEFAULT_PROMPT = (
    "這是一位展覽觀眾用手寫筆現場畫下的作品。請以它為起點，重新創作一幅完整的畫作，"
    "作為機器對這幅畫的回應。\n"
    "保留原作的主要構圖、形體位置與筆觸的動勢；內容、色彩、材質與畫面表現可以自由詮釋與延伸。\n"
    "即使原作只有零星筆跡，也請由它發展出完整的畫面。\n"
    "輸出為 16:9 橫向畫面，不要加入任何文字、標誌、簽名或邊框。"
)

ALLOWED_MODELS = ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare")
ALLOWED_QUALITIES = ("low", "medium", "high", "xhigh", "max")

DEFAULT_SETTINGS: dict[str, Any] = {
    "drawing_duration_seconds": 90,
    "turnaround_seconds": 10,
    "display_min_seconds": 10,
    "visitor_seconds": 10,
    "ai_seconds": 10,
    "artist_seconds": 10,
    "gallery_limit": 10,
    "fade_seconds": 1.0,
    "reference_opacity": 0.3,
    "ai_model": "gpt-image-2.5-sunburst",
    "ai_prompt": DEFAULT_PROMPT,
    "ai_quality": "medium",
    "ai_size": "2048x1152",
    "ai_timeout_seconds": 180,
}

_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


class SettingsError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _int_range(data: dict[str, Any], key: str, low: int, high: int) -> None:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SettingsError(key, f"必須是 {low} 到 {high} 之間的整數")


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """驗證完整設定，回傳正規化後的副本。"""
    unknown = set(data) - set(DEFAULT_SETTINGS)
    if unknown:
        raise SettingsError(sorted(unknown)[0], "未知的設定項目")
    result = dict(data)
    _int_range(result, "drawing_duration_seconds", 10, 600)
    _int_range(result, "turnaround_seconds", 0, 300)
    _int_range(result, "display_min_seconds", 1, 600)
    for field in ("visitor_seconds", "ai_seconds", "artist_seconds"):
        _int_range(result, field, 1, 600)
    _int_range(result, "gallery_limit", 1, 1000)
    fade = result["fade_seconds"]
    if isinstance(fade, bool) or not isinstance(fade, (int, float)) or not 0 <= fade <= 10:
        raise SettingsError("fade_seconds", "必須是 0 到 10 之間的秒數")
    result["fade_seconds"] = round(float(fade), 1)
    _int_range(result, "ai_timeout_seconds", 30, 900)

    opacity = result["reference_opacity"]
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not 0 <= opacity <= 1:
        raise SettingsError("reference_opacity", "必須是 0 到 1 之間的數字")
    result["reference_opacity"] = float(opacity)

    if result["ai_model"] not in ALLOWED_MODELS:
        raise SettingsError("ai_model", "不在允許清單內")
    if result["ai_quality"] not in ALLOWED_QUALITIES:
        raise SettingsError("ai_quality", "不支援的品質")

    match = _SIZE_RE.match(str(result["ai_size"]))
    if not match:
        raise SettingsError("ai_size", "格式必須是 WIDTHxHEIGHT")
    width, height = int(match.group(1)), int(match.group(2))
    if width % 16 or height % 16:
        raise SettingsError("ai_size", "寬高都必須是 16 的倍數")
    if not 1 / 3 <= width / height <= 3:
        raise SettingsError("ai_size", "比例必須介於 1:3 與 3:1")
    if width > 3840 or height > 3840 or not 655_360 <= width * height <= 8_294_400:
        raise SettingsError("ai_size", "超出模型支援的像素範圍")

    prompt = str(result["ai_prompt"]).strip()
    if not prompt:
        raise SettingsError("ai_prompt", "不可為空白")
    result["ai_prompt"] = prompt
    return result


def ensure_default_settings(conn: sqlite3.Connection) -> None:
    if state.get(conn, "current_settings_version_id") is not None:
        return
    version_id = _insert_version(conn, DEFAULT_SETTINGS, "初始設定")
    state.set(conn, "current_settings_version_id", version_id)


def _insert_version(conn: sqlite3.Connection, data: dict[str, Any], note: str | None) -> int:
    cursor = conn.execute(
        "INSERT INTO settings_versions (data_json, created_at, note) VALUES (?, ?, ?)",
        (json.dumps(data, ensure_ascii=False), now_iso(), note),
    )
    return int(cursor.lastrowid)


def get_version(conn: sqlite3.Connection, version_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT data_json FROM settings_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"settings version {version_id} not found")
    # 舊版本缺少的新欄位以預設值補齊
    return {**DEFAULT_SETTINGS, **json.loads(row["data_json"])}


def get_current(conn: sqlite3.Connection) -> tuple[int, dict[str, Any]]:
    version_id = int(state.get(conn, "current_settings_version_id"))
    return version_id, get_version(conn, version_id)


def update(
    conn: sqlite3.Connection, patch: dict[str, Any], note: str | None = None
) -> tuple[int, dict[str, Any]]:
    """套用部分修改並建立新版本；呼叫端負責包在 transaction 內。"""
    _, current = get_current(conn)
    merged = validate({**current, **patch})
    if merged == current:
        return get_current(conn)
    version_id = _insert_version(conn, merged, note)
    state.set(conn, "current_settings_version_id", version_id)
    return version_id, merged


def public_subset(data: dict[str, Any]) -> dict[str, Any]:
    """平板可見的設定（不含 AI 參數）。"""
    return {
        "drawing_duration_seconds": data["drawing_duration_seconds"],
        "turnaround_seconds": data["turnaround_seconds"],
        "reference_opacity": data["reference_opacity"],
    }
