"""時間工具：資料庫與 API 一律使用 UTC ISO 8601 字串（毫秒、Z 結尾）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return to_iso(now_utc())


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def add_seconds(value: datetime, seconds: float) -> datetime:
    return value + timedelta(seconds=seconds)
