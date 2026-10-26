"""OpenAI Images API（POST /v1/images/edits）供應商。

- 用戶端設定 max_retries=0：每件作品只送出一次付費請求，任何錯誤都直接失敗。
- timeout 使用工作綁定的 ai_timeout_seconds；整體期限另由 Worker 的期限包裝把關。
- 回應取 data[0].b64_json；GPT Image 模型一律回傳 base64，不傳 response_format。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import Any

import openai
from openai import OpenAI

from backend.ai_providers.base import AIError, ImageRequest, ImageResult

_FORMAT_MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
_REFUSAL_CODES = {"moderation_blocked", "content_policy_violation", "safety_violation"}


def _default_client_factory(api_key: str, timeout: float) -> Any:
    return OpenAI(api_key=api_key, max_retries=0, timeout=timeout)


def map_openai_error(error: Exception) -> AIError:
    """把 openai SDK 例外轉成 AIError；訊息不含金鑰與請求內容。"""
    request_id = getattr(error, "request_id", None)
    code_in_body = getattr(error, "code", None)
    message = getattr(error, "message", None) or str(error)

    # APITimeoutError 是 APIConnectionError 的子類別，必須先判斷
    if isinstance(error, openai.APITimeoutError):
        return AIError("timeout", "OpenAI 請求逾時", request_id)
    if isinstance(error, openai.APIConnectionError):
        return AIError("network", f"無法連線到 OpenAI：{message}", request_id)
    if isinstance(error, openai.RateLimitError):
        if code_in_body == "insufficient_quota":
            return AIError("quota", f"OpenAI 額度不足：{message}", request_id)
        return AIError("rate_limited", f"OpenAI 請求次數受限：{message}", request_id)
    if isinstance(error, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return AIError("auth", f"OpenAI 驗證或權限錯誤：{message}", request_id)
    if isinstance(error, openai.BadRequestError):
        if code_in_body in _REFUSAL_CODES:
            return AIError("refused", f"OpenAI 拒絕生成：{message}", request_id)
        return AIError("bad_request", f"OpenAI 請求參數錯誤：{message}", request_id)
    if isinstance(error, openai.InternalServerError):
        return AIError("server_error", f"OpenAI 伺服器錯誤：{message}", request_id)
    if isinstance(error, openai.APIStatusError):
        return AIError(f"http_{error.status_code}", f"OpenAI 回應錯誤：{message}", request_id)
    return AIError("openai_error", f"{type(error).__name__}: {message}", request_id)


class OpenAIImageProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        client_factory: Callable[[str, float], Any] = _default_client_factory,
    ) -> None:
        self._api_key = api_key
        self._client_factory = client_factory

    def generate(self, request: ImageRequest) -> ImageResult:
        try:
            image_bytes = request.input_png.read_bytes()
        except OSError as error:
            raise AIError("input_missing", f"無法讀取 AI 輸入圖：{error}") from error

        client = self._client_factory(self._api_key, request.timeout_seconds)
        try:
            response = client.images.edit(
                image=("ai-input.png", image_bytes, "image/png"),
                prompt=request.prompt,
                model=request.model,
                quality=request.quality,
                size=request.size,
                n=1,
                output_format="png",
            )
        except openai.OpenAIError as error:
            raise map_openai_error(error) from error
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        request_id = getattr(response, "_request_id", None)
        data = getattr(response, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if not b64:
            raise AIError("bad_response", "OpenAI 回應中沒有圖片資料", request_id)
        try:
            decoded = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise AIError("bad_response", "OpenAI 回傳的 base64 無法解碼", request_id) from error

        output_format = getattr(response, "output_format", None) or "png"
        usage = getattr(response, "usage", None)
        usage_dict = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else None
        return ImageResult(
            image_bytes=decoded,
            mime_type=_FORMAT_MIME.get(str(output_format), "image/png"),
            request_id=request_id,
            usage=usage_dict,
        )
