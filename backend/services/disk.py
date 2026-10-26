"""資料磁碟空間檢查：可用空間低於 DISK_DISCARD_RATIO 時捨棄新提交。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from backend.config import DISK_DISCARD_RATIO, get_paths


def _usage(path: Path) -> tuple[int, int, int]:
    """回傳 (total, used, free)；測試以 monkeypatch 取代。"""
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def disk_status() -> dict[str, Any]:
    data_dir = get_paths().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    total, _, free = _usage(data_dir)
    ratio = free / total if total else 0.0
    discarding = ratio < DISK_DISCARD_RATIO
    return {
        "ok": not discarding,
        "free_bytes": free,
        "total_bytes": total,
        "free_ratio": round(ratio, 4),
        "discarding": discarding,
    }


def is_discarding() -> bool:
    return bool(disk_status()["discarding"])
