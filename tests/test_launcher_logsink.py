"""集中日誌寫入器：多連線、順序、斷線隔離、格式與 logclient 串接。"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

from backend.logsink.server import LogSinkServer, normalize_record


def _record(message: str, **extra) -> bytes:
    data = {
        "ts": "2026-10-27T03:05:00.123Z",
        "level": "INFO",
        "source": "test",
        "pid": 42,
        "event": "test.event",
        "message": message,
        "ids": {},
        "exc": None,
    }
    data.update(extra)
    return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


def _wait_written(sink: LogSinkServer, count: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while sink.written < count and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sink.written >= count


@pytest.fixture
def sink(tmp_path: Path):
    server = LogSinkServer("127.0.0.1", 0, tmp_path / "logs" / "runtime.txt")
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _lines(sink: LogSinkServer) -> list[str]:
    return sink.log_path.read_text(encoding="utf-8").splitlines()


def test_multiple_connections_keep_per_connection_order(sink: LogSinkServer) -> None:
    connections = [socket.create_connection(("127.0.0.1", sink.port)) for _ in range(3)]
    for index in range(20):
        for number, connection in enumerate(connections):
            connection.sendall(_record(f"conn{number}-{index:02d}"))
    for connection in connections:
        connection.close()
    _wait_written(sink, 60)

    lines = _lines(sink)
    assert len(lines) == 60
    for number in range(3):
        mine = [line.split(" test.event ")[1] for line in lines if f" conn{number}-" in line]
        assert mine == [f"conn{number}-{index:02d}" for index in range(20)]


def test_disconnect_does_not_affect_other_connections(sink: LogSinkServer) -> None:
    first = socket.create_connection(("127.0.0.1", sink.port))
    second = socket.create_connection(("127.0.0.1", sink.port))
    first.sendall(_record("第一條"))
    first.sendall('{"ts": "2026-10-27T03:05:00.123Z", "message": "半行'.encode("utf-8"))  # 斷在行中間
    first.close()
    second.sendall(_record("第二條仍可寫入"))
    _wait_written(sink, 2)
    time.sleep(0.2)
    second.sendall(_record("斷線後再寫"))
    second.close()
    _wait_written(sink, 3)

    text = "\n".join(_lines(sink))
    assert "第一條" in text
    assert "第二條仍可寫入" in text
    assert "斷線後再寫" in text


def test_line_format_and_malformed_input(sink: LogSinkServer) -> None:
    connection = socket.create_connection(("127.0.0.1", sink.port))
    connection.sendall(
        _record(
            "生成失敗 key=sk-abcdefghijklmnop",
            level="ERROR",
            ids={"session_id": "abc", "ai_job_id": 3, "empty": None},
            exc="Traceback\n  line 1",
        )
    )
    connection.sendall(b"not json\n")
    connection.close()
    _wait_written(sink, 2)

    lines = _lines(sink)
    assert "[ERROR] test#42 test.event 生成失敗" in lines[0]
    assert lines[0].endswith("| session_id=abc ai_job_id=3")
    assert "sk-abcdefghijklmnop" not in lines[0]
    assert lines[1] == "    Traceback"
    assert any("[WARNING]" in line and "logsink.malformed" in line for line in lines[3:])


def test_normalize_record_fills_missing_fields() -> None:
    record = normalize_record({"level": "debug", "ts": "not-a-time", "ids": "bad"})
    assert record["level"] == "INFO"
    assert record["source"] == "unknown"
    assert record["event"] == "unknown"
    assert record["ids"] == {}
    assert record["ts"].endswith("Z")
    assert normalize_record(["not", "a", "dict"])["message"] == ""


def test_port_in_use_raises(sink: LogSinkServer, tmp_path: Path) -> None:
    other = LogSinkServer("127.0.0.1", sink.port, tmp_path / "other.txt")
    with pytest.raises(OSError):
        other.start()


def test_logclient_sends_through_sink(paths_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend import logclient

    server = LogSinkServer("127.0.0.1", 0, paths_env.runtime_log)
    server.start()
    try:
        monkeypatch.setenv("AP_DRAWART_LOG_SINK", server.address)
        logger = logclient.get_logger("web")
        logger.info("session.start", "開始場次", session_id="abc123")
        logger.warning("disk.low", "磁碟空間不足")
        assert logclient.status()["mode"] == "sink"
        logclient.close(3.0)
        _wait_written(server, 2)
    finally:
        server.stop()

    text = paths_env.runtime_log.read_text(encoding="utf-8")
    assert "[INFO] web#" in text
    assert "session.start 開始場次 | session_id=abc123" in text
    assert "disk.low 磁碟空間不足" in text
