"""AI 供應商選擇。"""

from backend.ai_providers.base import AIError, ImageProvider, ImageRequest, ImageResult
from backend.config import ai_provider_name, read_openai_key

__all__ = ["AIError", "ImageProvider", "ImageRequest", "ImageResult", "get_provider"]


def get_provider() -> ImageProvider:
    """依 AP_DRAWART_AI_PROVIDER（openai / fake）回傳供應商；每次呼叫重新讀取金鑰。"""
    name = ai_provider_name()
    if name == "fake":
        from backend.ai_providers.fake import FakeImageProvider

        return FakeImageProvider.from_env()
    if name == "openai":
        api_key, _ = read_openai_key()
        if not api_key:
            raise AIError("not_configured", "尚未設定 OpenAI 金鑰")
        from backend.ai_providers.openai_images import OpenAIImageProvider

        return OpenAIImageProvider(api_key)
    raise AIError("not_configured", f"未知的 AI 供應商：{name}")
