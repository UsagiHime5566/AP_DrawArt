"""開發與測試用的假供應商：不連網、不計費。

延遲 AP_DRAWART_FAKE_DELAY_SECONDS 秒後，依 AP_DRAWART_FAKE_FAIL_RATE 隨機失敗；
成功時以 Pillow 將輸入圖上色、加粗並疊上幾何色塊，輸出要求的尺寸。
"""

from __future__ import annotations

import io
import os
import random
import time
import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from backend.ai_providers.base import AIError, ImageRequest, ImageResult

_PALETTE = [
    (255, 59, 107),
    (31, 91, 255),
    (255, 212, 0),
    (0, 179, 138),
    (255, 120, 40),
    (140, 70, 255),
]


def _parse_size(size: str) -> tuple[int, int]:
    width, _, height = size.partition("x")
    try:
        return int(width), int(height)
    except ValueError as error:
        raise AIError("bad_request", f"無效的輸出尺寸：{size}") from error


def _stylize(source: Image.Image, width: int, height: int, rng: random.Random) -> Image.Image:
    gray = source.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    # 加粗深色筆跡，讓結果明顯不同於原作
    gray = gray.filter(ImageFilter.MinFilter(9))
    dark, light = rng.sample(_PALETTE, 2)
    light = tuple(min(255, channel + 150) for channel in light)
    colored = ImageOps.colorize(ImageOps.autocontrast(gray), black=dark, white=light).convert("RGBA")

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(7):
        color = (*rng.choice(_PALETTE), rng.randint(60, 120))
        x0 = rng.randint(-width // 4, width)
        y0 = rng.randint(-height // 4, height)
        x1 = x0 + rng.randint(width // 10, width // 2)
        y1 = y0 + rng.randint(height // 10, height // 2)
        if rng.random() < 0.5:
            draw.ellipse((x0, y0, x1, y1), fill=color)
        else:
            draw.rectangle((x0, y0, x1, y1), fill=color)
    return Image.alpha_composite(colored, overlay).convert("RGB")


class FakeImageProvider:
    name = "fake"

    def __init__(self, delay_seconds: float = 5.0, fail_rate: float = 0.0, seed: int | None = None) -> None:
        self.delay_seconds = delay_seconds
        self.fail_rate = fail_rate
        self._rng = random.Random(seed)

    @classmethod
    def from_env(cls) -> FakeImageProvider:
        return cls(
            delay_seconds=float(os.environ.get("AP_DRAWART_FAKE_DELAY_SECONDS", "5")),
            fail_rate=float(os.environ.get("AP_DRAWART_FAKE_FAIL_RATE", "0")),
        )

    def generate(self, request: ImageRequest) -> ImageResult:
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if self._rng.random() < self.fail_rate:
            raise AIError("fake_failure", "假供應商模擬生成失敗")

        width, height = _parse_size(request.size)
        try:
            with Image.open(request.input_png) as source:
                art = _stylize(source, width, height, self._rng)
        except OSError as error:
            raise AIError("input_missing", f"無法讀取 AI 輸入圖：{error}") from error

        buffer = io.BytesIO()
        art.save(buffer, "PNG")
        return ImageResult(
            image_bytes=buffer.getvalue(),
            mime_type="image/png",
            request_id=f"fake-{uuid.uuid4().hex[:12]}",
            usage={"provider": "fake"},
        )
