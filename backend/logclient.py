"""執行日誌客戶端。

各程序以 get_logger(來源) 取得記錄器。設定 AP_DRAWART_LOG_SINK=host:port 時，
事件以 JSON 行送往集中日誌寫入器（backend.logsink），由它單獨寫入 logs/runtime.txt；
未設定時（開發、測試）直接附加寫入同一檔案。

    log = get_logger("web")
    log.info("session.start", "開始場次", session_id=sid)
    log.error("ai.failed", "生成失敗", ai_job_id=3, exc=traceback_text)

事件名稱採「區域.動作」，例如 session.start、submission.saved、ai.failed、playback.commit。
不可傳入金鑰、授權標頭、圖片 Base64 或二進位內容；format_line 另有遮罩作為保險。
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import socket
import sys
import threading
import traceback
from typing import Any

from backend.config import get_paths, log_sink_address
from backend.timeutil import now_iso, parse_iso

LEVELS = ("INFO", "WARNING", "ERROR")

_MASK_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
    (re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\s\"',]+"), r"\1***"),
)


def mask(text: str) -> str:
    for pattern, replacement in _MASK_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def format_line(record: dict[str, Any]) -> str:
    """把事件轉成 runtime.txt 的一行（例外堆疊縮排接在後面），以換行結尾。"""
    local_ts = parse_iso(record["ts"]).astimezone().isoformat(timespec="milliseconds")
    ids = record.get("ids") or {}
    ids_text = " ".join(f"{key}={value}" for key, value in ids.items() if value is not None)
    line = (
        f"{local_ts} [{record['level']}] {record['source']}#{record['pid']} "
        f"{record['event']} {record['message']}"
    )
    if ids_text:
        line += f" | {ids_text}"
    exc = record.get("exc")
    if exc:
        line += "\n" + "\n".join("    " + part for part in str(exc).rstrip().splitlines())
    return mask(line) + "\n"


class _FileTransport:
    """開發模式：直接附加寫檔（同一程序內以鎖保護）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def send(self, record: dict[str, Any]) -> None:
        path = get_paths().runtime_log
        line = format_line(record)
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
                    handle.flush()
                self.last_error = None
            except OSError as error:
                self.last_error = f"{type(error).__name__}: {error}"
                sys.stderr.write(line)


class _SinkTransport:
    """正式模式：背景執行緒把 JSON 行送往集中日誌寫入器，斷線自動重連。"""

    def __init__(self, address: tuple[str, int]) -> None:
        self.address = address
        self.last_error: str | None = None
        self.dropped = 0
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10_000)
        self._sock: socket.socket | None = None
        self._thread = threading.Thread(target=self._run, name="log-sink-client", daemon=True)
        self._thread.start()
        atexit.register(self.flush, 2.0)

    def send(self, record: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def flush(self, timeout: float) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        pending: dict[str, Any] | None = None
        while True:
            if pending is None:
                pending = self._queue.get()
                if pending is None:
                    return
            try:
                if self._sock is None:
                    self._sock = socket.create_connection(self.address, timeout=3)
                payload = json.dumps(pending, ensure_ascii=False) + "\n"
                self._sock.sendall(payload.encode("utf-8"))
                pending = None
                self.last_error = None
            except OSError as error:
                self.last_error = f"{type(error).__name__}: {error}"
                sys.stderr.write(format_line(pending))
                pending = None
                if self._sock is not None:
                    self._sock.close()
                    self._sock = None
                threading.Event().wait(1.0)


_file_transport = _FileTransport()
_sink_transport: _SinkTransport | None = None
_sink_lock = threading.Lock()


def _transport() -> _FileTransport | _SinkTransport:
    global _sink_transport
    address = log_sink_address()
    if address is None:
        return _file_transport
    with _sink_lock:
        if _sink_transport is None or _sink_transport.address != address:
            _sink_transport = _SinkTransport(address)
        return _sink_transport


def close(timeout: float = 2.0) -> None:
    """送出尚未寫出的日誌並結束背景傳送；程序結束前呼叫，之後的記錄會重新建立連線。"""
    global _sink_transport
    with _sink_lock:
        transport, _sink_transport = _sink_transport, None
    if transport is not None:
        transport.flush(timeout)


def status() -> dict[str, Any]:
    """供健康檢查使用：日誌是否正常寫出。"""
    transport = _transport()
    mode = "sink" if isinstance(transport, _SinkTransport) else "file"
    return {
        "mode": mode,
        "ok": transport.last_error is None,
        "last_error": transport.last_error,
        "dropped": getattr(transport, "dropped", 0),
    }


class RuntimeLogger:
    def __init__(self, source: str) -> None:
        self.source = source

    def _emit(self, level: str, event: str, message: str, exc: str | None, ids: dict[str, Any]) -> None:
        record = {
            "ts": now_iso(),
            "level": level,
            "source": self.source,
            "pid": os.getpid(),
            "event": event,
            "message": message,
            "ids": ids,
            "exc": exc,
        }
        _transport().send(record)

    def info(self, event: str, message: str, **ids: Any) -> None:
        self._emit("INFO", event, message, None, ids)

    def warning(self, event: str, message: str, exc: str | None = None, **ids: Any) -> None:
        self._emit("WARNING", event, message, exc, ids)

    def error(self, event: str, message: str, exc: str | None = None, **ids: Any) -> None:
        self._emit("ERROR", event, message, exc, ids)

    def exception(self, event: str, message: str, **ids: Any) -> None:
        """在 except 區塊中呼叫，附上目前例外的堆疊。"""
        self._emit("ERROR", event, message, traceback.format_exc(), ids)


def get_logger(source: str) -> RuntimeLogger:
    return RuntimeLogger(source)
