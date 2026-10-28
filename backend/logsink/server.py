"""集中日誌寫入器的 TCP 伺服器。

協定：每行一筆 UTF-8 JSON（backend.logclient 送出的格式）。
多條連線各自讀取，事件放進同一個佇列，由單一寫入執行緒依到達順序附加寫入；
同一條連線內的順序保持不變，某條連線斷開不影響其他連線。
"""

from __future__ import annotations

import json
import os
import queue
import socket
import socketserver
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.config import get_paths
from backend.logclient import LEVELS, format_line
from backend.timeutil import now_iso, parse_iso

DEFAULT_PORT = 8765
_MAX_LINE_BYTES = 1_048_576


def make_record(level: str, event: str, message: str, source: str = "logsink", **ids: Any) -> dict[str, Any]:
    return {
        "ts": now_iso(),
        "level": level,
        "source": source,
        "pid": os.getpid(),
        "event": event,
        "message": message,
        "ids": ids,
        "exc": None,
    }


def normalize_record(obj: Any) -> dict[str, Any]:
    """補齊缺漏欄位，確保 format_line 一定能輸出。"""
    if not isinstance(obj, dict):
        obj = {}
    level = str(obj.get("level") or "INFO").upper()
    if level not in LEVELS:
        level = "INFO"
    ts = obj.get("ts")
    try:
        parse_iso(str(ts))
    except (TypeError, ValueError):
        ts = now_iso()
    ids = obj.get("ids")
    exc = obj.get("exc")
    return {
        "ts": ts,
        "level": level,
        "source": str(obj.get("source") or "unknown"),
        "pid": obj.get("pid", "?"),
        "event": str(obj.get("event") or "unknown"),
        "message": str(obj.get("message", "")),
        "ids": ids if isinstance(ids, dict) else {},
        "exc": str(exc) if exc else None,
    }


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def setup(self) -> None:
        super().setup()
        self.server.track(self.connection)

    def handle(self) -> None:
        while True:
            try:
                raw = self.rfile.readline(_MAX_LINE_BYTES)
            except OSError:
                return
            if not raw:
                return
            line = raw.strip()
            if line:
                self.server.sink.submit_raw(line)

    def finish(self) -> None:
        try:
            super().finish()
        except OSError:
            pass
        self.server.untrack(self.connection)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], sink: LogSinkServer) -> None:
        self.sink = sink
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        super().__init__(address, _Handler)

    def track(self, connection: socket.socket) -> None:
        with self._connections_lock:
            self._connections.add(connection)

    def untrack(self, connection: socket.socket) -> None:
        with self._connections_lock:
            self._connections.discard(connection)

    def close_all_connections(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class LogSinkServer:
    """可在程序內啟動的日誌寫入器。port=0 時使用系統分配的連線埠。

    listener（可選）在寫入執行緒中收到每筆已整理的事件，例如啟動器主控台的「最近事件」。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        log_path: Path | None = None,
        listener: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._log_path = log_path
        self._listener = listener
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._server: _Server | None = None
        self._serve_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.written = 0

    @property
    def log_path(self) -> Path:
        return self._log_path or get_paths().runtime_log

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def start(self) -> tuple[str, int]:
        """綁定連線埠並開始服務；連線埠被占用時拋出 OSError。"""
        server = _Server((self.host, self.port), self)
        self._server = server
        self.port = int(server.server_address[1])
        self._writer_thread = threading.Thread(target=self._write_loop, name="logsink-writer", daemon=True)
        self._writer_thread.start()
        self._serve_thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.2}, name="logsink-server", daemon=True
        )
        self._serve_thread.start()
        return self.host, self.port

    def stop(self, timeout: float = 5.0) -> None:
        """停止接收新資料，寫完佇列中剩餘事件後關閉檔案。"""
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.close_all_connections()
            server.server_close()
        if self._writer_thread is not None:
            self._queue.put(None)
            self._writer_thread.join(timeout)
            self._writer_thread = None

    def submit(self, record: dict[str, Any]) -> None:
        self._queue.put(normalize_record(record))

    def submit_raw(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace")
        try:
            obj = json.loads(text)
        except ValueError:
            self.submit(
                make_record("WARNING", "logsink.malformed", f"收到無法解析的日誌資料：{text[:200]}")
            )
            return
        self.submit(obj)

    def _write_loop(self) -> None:
        handle = None
        while True:
            record = self._queue.get()
            if record is None:
                break
            line = format_line(record)
            try:
                if handle is None:
                    path = self.log_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handle = open(path, "a", encoding="utf-8", newline="\n")
                handle.write(line)
                handle.flush()
                self.written += 1
                self.last_error = None
            except OSError as error:
                self.last_error = f"{type(error).__name__}: {error}"
                if sys.stderr is not None:
                    sys.stderr.write(line)
                if handle is not None:
                    handle.close()
                    handle = None
            if self._listener is not None:
                try:
                    self._listener(record)
                except Exception:  # noqa: BLE001 - 顯示端出錯不能影響寫檔
                    pass
        if handle is not None:
            handle.close()
