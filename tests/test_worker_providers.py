"""AI 供應商：OpenAI 呼叫方式與錯誤對應、假供應商、供應商選擇與後臺試生成。"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pytest
from PIL import Image

from backend.ai_providers import AIError, ImageRequest, get_provider
from backend.ai_providers import openai_images
from backend.ai_providers.fake import FakeImageProvider
from backend.ai_providers.openai_images import OpenAIImageProvider, map_openai_error
from backend.worker.ai_test import run_ai_test

_REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/images/edits")


def _png_bytes(size=(2048, 1152)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "teal").save(buffer, "PNG")
    return buffer.getvalue()


def _input_png(tmp_path: Path) -> Path:
    path = tmp_path / "ai-input.png"
    Image.new("RGB", (1920, 1080), "white").save(path)
    return path


def _status_error(cls, status: int, body: dict | None = None):
    response = httpx2.Response(status, request=_REQUEST, headers={"x-request-id": "req_err"})
    return cls("error message", response=response, body=body)


class _FakeImages:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.kwargs: dict | None = None

    def edit(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeClient:
    instances: list[_FakeClient] = []
    outcome: object = None

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.images = _FakeImages(type(self).outcome)
        self.closed = False
        type(self).instances.append(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_openai(monkeypatch):
    _FakeClient.instances = []
    monkeypatch.setattr(openai_images, "OpenAI", _FakeClient)
    return _FakeClient


def _request(tmp_path: Path, timeout: float = 42.0) -> ImageRequest:
    return ImageRequest(
        input_png=_input_png(tmp_path),
        prompt="自由重繪",
        model="gpt-image-2.5-sunburst",
        quality="medium",
        size="2048x1152",
        timeout_seconds=timeout,
    )


def test_openai_provider_uses_single_attempt_client(fake_openai, tmp_path):
    png = _png_bytes()
    fake_openai.outcome = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png).decode())],
        output_format="png",
        usage=None,
        _request_id="req_abc",
    )

    result = OpenAIImageProvider("sk-test-key-123456").generate(_request(tmp_path))

    client = fake_openai.instances[0]
    assert client.init_kwargs == {"api_key": "sk-test-key-123456", "max_retries": 0, "timeout": 42.0}
    kwargs = client.images.kwargs
    assert kwargs["model"] == "gpt-image-2.5-sunburst"
    assert kwargs["quality"] == "medium" and kwargs["size"] == "2048x1152"
    assert kwargs["prompt"] == "自由重繪" and kwargs["n"] == 1
    assert kwargs["output_format"] == "png"
    assert "response_format" not in kwargs
    name, data, mime = kwargs["image"]
    assert name == "ai-input.png" and mime == "image/png" and data.startswith(b"\x89PNG")
    assert result.image_bytes == png and result.mime_type == "image/png"
    assert result.request_id == "req_abc"
    assert client.closed


def test_openai_provider_maps_sdk_error(fake_openai, tmp_path):
    fake_openai.outcome = _status_error(openai.RateLimitError, 429)

    with pytest.raises(AIError) as error:
        OpenAIImageProvider("sk-test").generate(_request(tmp_path))

    assert error.value.code == "rate_limited"
    assert error.value.request_id == "req_err"
    assert fake_openai.instances[0].closed


def test_openai_provider_rejects_empty_response(fake_openai, tmp_path):
    fake_openai.outcome = SimpleNamespace(data=[], output_format="png", usage=None)

    with pytest.raises(AIError) as error:
        OpenAIImageProvider("sk-test").generate(_request(tmp_path))
    assert error.value.code == "bad_response"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (openai.APITimeoutError(request=_REQUEST), "timeout"),
        (openai.APIConnectionError(request=_REQUEST), "network"),
        (_status_error(openai.RateLimitError, 429), "rate_limited"),
        (_status_error(openai.RateLimitError, 429, {"code": "insufficient_quota"}), "quota"),
        (_status_error(openai.AuthenticationError, 401), "auth"),
        (_status_error(openai.PermissionDeniedError, 403), "auth"),
        (_status_error(openai.BadRequestError, 400, {"code": "moderation_blocked"}), "refused"),
        (_status_error(openai.BadRequestError, 400, {"code": "invalid_value"}), "bad_request"),
        (_status_error(openai.InternalServerError, 500), "server_error"),
        (_status_error(openai.NotFoundError, 404), "http_404"),
    ],
)
def test_map_openai_error(error, code):
    mapped = map_openai_error(error)
    assert mapped.code == code
    assert "sk-" not in mapped.message


def test_fake_provider_output_matches_requested_size(tmp_path):
    request = _request(tmp_path)

    result = FakeImageProvider(delay_seconds=0, seed=3).generate(request)

    with Image.open(io.BytesIO(result.image_bytes)) as image:
        assert image.format == "PNG" and image.size == (2048, 1152)
    with pytest.raises(AIError) as error:
        FakeImageProvider(delay_seconds=0, fail_rate=1.0).generate(request)
    assert error.value.code == "fake_failure"


def test_get_provider_selection(monkeypatch, paths_env):
    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "fake")
    monkeypatch.setenv("AP_DRAWART_FAKE_DELAY_SECONDS", "0.5")
    provider = get_provider()
    assert isinstance(provider, FakeImageProvider) and provider.delay_seconds == 0.5

    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "openai")
    with pytest.raises(AIError) as error:
        get_provider()
    assert error.value.code == "not_configured"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    assert isinstance(get_provider(), OpenAIImageProvider)

    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "unknown")
    with pytest.raises(AIError):
        get_provider()


def test_run_ai_test_writes_files(db_conn, paths_env, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRAWART_FAKE_DELAY_SECONDS", "0")
    image_path = tmp_path / "upload.jpg"
    Image.new("RGB", (1600, 900), "white").save(image_path, "JPEG")

    result = run_ai_test(image_path, {"ai_quality": "high", "ai_size": "1536x864"})

    assert result["status"] == "succeeded"
    assert result["quality"] == "high" and result["size"] == "1536x864"
    assert result["model"] == "gpt-image-2.5-sunburst"
    assert result["result_url"] == f"/media/ai-tests/{result['id']}/result.png?v=1"
    directory = paths_env.ai_tests_dir / result["id"]
    with Image.open(directory / "input.png") as source:
        assert source.format == "PNG"
    with Image.open(directory / "result.png") as output:
        assert output.size == (1536, 864)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "succeeded" and metadata["quality"] == "high"
    assert "admin.ai_test" in paths_env.runtime_log.read_text(encoding="utf-8")
    # 試生成不進入佇列
    assert db_conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 0


def test_run_ai_test_rejects_invalid_override(db_conn, tmp_path):
    image_path = _input_png(tmp_path)
    result = run_ai_test(image_path, {"ai_size": "1000x1000"})
    assert result["status"] == "failed" and result["error_code"] == "settings_invalid"
    assert result["result_url"] is None


def test_run_ai_test_rejects_invalid_image(db_conn, tmp_path):
    image_path = tmp_path / "broken.png"
    image_path.write_bytes(b"not an image")
    result = run_ai_test(image_path, {})
    assert result["status"] == "failed" and result["error_code"] == "invalid_image"
