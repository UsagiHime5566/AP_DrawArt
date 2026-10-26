"""後臺讀取 logs/runtime.txt：分段、增量讀取，只回傳完整的行。"""

from __future__ import annotations

from typing import Any

from backend.config import get_paths

DEFAULT_MAX_BYTES = 64 * 1024
MAX_MAX_BYTES = 1024 * 1024


def read_chunk(offset: int | None, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """offset 為 None 或超過檔案大小時回傳最後一段；否則從 offset 往後讀。"""
    max_bytes = max(1, min(max_bytes, MAX_MAX_BYTES))
    path = get_paths().runtime_log
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"offset": 0, "next_offset": 0, "size": 0, "text": ""}

    with open(path, "rb") as handle:
        if offset is None or offset < 0 or offset > size:
            start = max(0, size - max_bytes)
            handle.seek(start)
            chunk = handle.read(size - start)
            if start > 0:
                # 從下一個完整行開始
                newline = chunk.find(b"\n")
                if newline < 0:
                    chunk = b""
                    start = size
                else:
                    chunk = chunk[newline + 1 :]
                    start += newline + 1
        else:
            start = offset
            handle.seek(start)
            chunk = handle.read(max_bytes)

    # 不回傳還沒寫完的最後一行；單行超過 max_bytes 時才原樣回傳
    if chunk and not chunk.endswith(b"\n"):
        last_newline = chunk.rfind(b"\n")
        if last_newline >= 0:
            chunk = chunk[: last_newline + 1]
        elif len(chunk) < max_bytes:
            chunk = b""

    return {
        "offset": start,
        "next_offset": start + len(chunk),
        "size": size,
        "text": chunk.decode("utf-8", errors="replace"),
    }
