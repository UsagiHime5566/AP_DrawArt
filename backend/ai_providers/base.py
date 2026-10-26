"""AI 圖片供應商介面（共用，由整合者維護）。

Worker 與後臺試生成都只透過這個介面呼叫雲端，不直接使用 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ImageRequest:
    input_png: Path          # 實際送出的圖片（ai-input.png）
    prompt: str
    model: str
    quality: str
    size: str                # "WIDTHxHEIGHT"
    timeout_seconds: float


@dataclass(frozen=True)
class ImageResult:
    image_bytes: bytes       # 供應商回傳的原始圖片內容
    mime_type: str           # 例如 "image/png"
    request_id: str | None = None
    usage: dict[str, Any] | None = field(default=None)


class AIError(Exception):
    """任何生成失敗；code 例如 timeout、rate_limited、refused、auth、network、bad_response。"""

    def __init__(self, code: str, message: str, request_id: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


class ImageProvider(Protocol):
    name: str

    def generate(self, request: ImageRequest) -> ImageResult:
        """同步呼叫，必須在 timeout_seconds 內回傳或拋出 AIError，且不得自動重試。"""
        ...
