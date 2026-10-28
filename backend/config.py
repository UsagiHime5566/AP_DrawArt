"""路徑、連線埠與本機設定檔。

所有路徑都透過 get_paths() 依環境變數即時計算，測試可用環境變數改到暫存目錄。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 邏輯畫布尺寸（平板座標與 visitor.png 尺寸）
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080

# 可用空間低於此比例時捨棄新提交
DISK_DISCARD_RATIO = 0.02

# AI 失敗展示文字（已確認）
FAILURE_TEXT = "此刻，機器沉默。"

DISPLAY_ROLES = ("visitor", "ai", "artist")


@dataclass(frozen=True)
class Paths:
    root: Path
    data_dir: Path
    logs_dir: Path
    config_dir: Path
    base_image: Path
    static_dir: Path

    @property
    def db_file(self) -> Path:
        return self.data_dir / "exhibition.sqlite3"

    @property
    def artworks_dir(self) -> Path:
        return self.data_dir / "artworks"

    @property
    def submissions_dir(self) -> Path:
        return self.data_dir / "submissions"

    @property
    def ai_tests_dir(self) -> Path:
        return self.data_dir / "ai-tests"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def runtime_log(self) -> Path:
        return self.logs_dir / "runtime.txt"

    @property
    def displays_config(self) -> Path:
        return self.config_dir / "displays.json"

    @property
    def openai_key_file(self) -> Path:
        return self.config_dir / "openai.local.json"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.logs_dir,
            self.config_dir,
            self.artworks_dir,
            self.submissions_dir,
            self.ai_tests_dir,
            self.tmp_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def get_paths() -> Paths:
    root = _env_path("AP_DRAWART_ROOT", PROJECT_ROOT)
    default_static = root / "frontend" / "dist"
    if not default_static.exists() and (root / "app" / "static").exists():
        default_static = root / "app" / "static"
    return Paths(
        root=root,
        data_dir=_env_path("AP_DRAWART_DATA_DIR", root / "data"),
        logs_dir=_env_path("AP_DRAWART_LOGS_DIR", root / "logs"),
        config_dir=_env_path("AP_DRAWART_CONFIG_DIR", root / "config"),
        base_image=_env_path("AP_DRAWART_BASE_IMAGE", root / "base.webp"),
        static_dir=_env_path("AP_DRAWART_STATIC_DIR", default_static),
    )


def http_host() -> str:
    return os.environ.get("AP_DRAWART_HOST", "0.0.0.0")


def http_port() -> int:
    return int(os.environ.get("AP_DRAWART_PORT", "8080"))


def log_sink_address() -> tuple[str, int] | None:
    """集中日誌寫入器位址；未設定時各程序直接寫檔（僅限開發）。"""
    value = os.environ.get("AP_DRAWART_LOG_SINK")
    if not value:
        return None
    host, _, port = value.rpartition(":")
    return (host or "127.0.0.1", int(port))


def ai_provider_name() -> str:
    return os.environ.get("AP_DRAWART_AI_PROVIDER", "openai")


class ConfigFileError(ValueError):
    """本機設定檔存在但不是有效的 JSON 物件；訊息只含檔名與位置，不含檔案內容（可能是金鑰）。"""


# 手動編輯時常見的「最後一項後面多一個逗號」
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _read_json(path: Path) -> dict[str, Any] | None:
    """讀取設定檔；不存在時回傳 None，格式錯誤時拋出 ConfigFileError。

    設定檔可能被現場人員用記事本修改，因此接受 UTF-8 BOM 與結尾多餘的逗號。
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as strict_error:
        try:
            data = json.loads(_TRAILING_COMMA.sub(r"\1", text))
        except json.JSONDecodeError:
            raise ConfigFileError(
                f"{path.name} 不是有效的 JSON（第 {strict_error.lineno} 行第 {strict_error.colno} 字）"
            ) from None
    if not isinstance(data, dict):
        raise ConfigFileError(f"{path.name} 的內容必須是 JSON 物件（以大括號包住）")
    return data


def config_file_error(path: Path) -> str | None:
    """設定檔格式錯誤時回傳原因，供健康檢查、後臺與啟動器日誌顯示；正常或不存在時回傳 None。"""
    try:
        _read_json(path)
    except ConfigFileError as error:
        return str(error)
    except OSError as error:
        return f"{path.name} 無法讀取：{type(error).__name__}"
    return None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_openai_key() -> tuple[str | None, str | None]:
    """回傳 (金鑰, 來源)；來源為 "env"、"file" 或 None。

    金鑰檔格式錯誤時視為未設定，不讓整個服務失敗；原因由 config_file_error 提供。
    """
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key, "env"
    try:
        data = _read_json(get_paths().openai_key_file)
    except (ConfigFileError, OSError):
        return None, None
    if data and data.get("api_key"):
        return str(data["api_key"]), "file"
    return None, None


def write_openai_key(api_key: str) -> None:
    _write_json_atomic(get_paths().openai_key_file, {"api_key": api_key})


DEFAULT_DISPLAYS_CONFIG: dict[str, Any] = {
    "exhibition": {"monitor": 0},
    "visitor": {"monitor": 0},
    "ai": {"monitor": 1},
    "artist": {"monitor": 2},
    "browser": "auto",
}


def read_displays_config() -> dict[str, Any]:
    """螢幕配置；檔案格式錯誤時改用預設值（原因由 config_file_error 提供）。"""
    try:
        data = _read_json(get_paths().displays_config)
    except (ConfigFileError, OSError):
        data = None
    merged = json.loads(json.dumps(DEFAULT_DISPLAYS_CONFIG))
    if data:
        merged.update(data)
        if "exhibition" not in data:
            merged["exhibition"] = data.get("visitor", {"monitor": 0})
    return merged


def write_displays_config(data: dict[str, Any]) -> None:
    _write_json_atomic(get_paths().displays_config, data)
