"""啟動器主控台畫面：寬度、截斷、內容與事件過濾（不開真的主控台）。"""

from __future__ import annotations

import re
import socket
import time
from pathlib import Path

from launcher.console import DashboardState, display_width, fit, render_lines, summarize_record

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _plain(lines: list[str]) -> list[str]:
    return [_ANSI.sub("", line) for line in lines]


def _overview(**overrides) -> dict:
    data = {
        "intake_enabled": True,
        "health": {
            "status": "ok",
            "admin_urls": ["http://192.168.1.20:8080/admin"],
            "checks": {
                "database": {"ok": True},
                "worker": {"ok": True},
                "displays": {"ok": True},
                "disk": {"ok": True, "free_bytes": 5 * 1024**3, "free_ratio": 0.42, "discarding": False},
                "openai_key": {"ok": True, "source": "file"},
            },
        },
        "playback": {"mode": "gallery", "stage": "ai", "kind": "history", "receipt_order": 7, "waiting_for_ai": False},
        "counts": {"ai_queued": 1, "ai_running": 0, "submissions_total": 12, "played_total": 10, "playback_unplayed": 2},
    }
    data.update(overrides)
    return data


def test_display_width_and_fit() -> None:
    assert display_width("abc") == 3
    assert display_width("展覽") == 4
    assert display_width("（）") == 4
    assert fit("展覽系統", 8) == "展覽系統"
    cut = fit("展覽系統尚未就緒", 9)
    assert cut.endswith("...") and display_width(cut) <= 9


def test_render_shows_urls_and_status_within_screen() -> None:
    state = DashboardState(port=8080, started=0.0, phase="運作中", overview=_overview(), overview_ok=True)
    state.events.append(("20:00:01", "INFO", "Web 服務啟動"))
    state.events.append(("20:00:02", "WARNING", "展覽視窗未連線"))
    lines = render_lines(state, 80, 30, now=3725.0, clock="20:00:03")
    plain = _plain(lines)
    assert len(lines) <= 30
    assert all(display_width(line) < 80 for line in plain)
    text = "\n".join(plain)
    assert "運行 01:02:05" in text
    assert "http://192.168.1.20:8080/admin" in text
    assert "http://127.0.0.1:8080/admin" in text
    assert "系統    正常" in text
    assert "重播AI 作品 #7" in text and "接待：開放" in text
    assert "排隊 1 件" in text and "已收 12 件" in text
    assert "20:00:02 警告 展覽視窗未連線" in text
    assert "stop-exhibition.bat" in plain[-1]
    # 警告事件以黃色顯示
    assert any(line.startswith("\x1b[33m") and "展覽視窗未連線" in line for line in lines)


def test_render_before_ready_and_with_key_error() -> None:
    waiting = DashboardState(port=8080, started=0.0, phase="等待 Web 服務就緒")
    text = "\n".join(_plain(render_lines(waiting, 100, 25, 1.0, "20:00:00")))
    assert "等待 Web 服務就緒後顯示" in text and "系統    等待 Web 服務就緒" in text

    health = _overview()["health"]
    health["status"] = "setup_required"
    health["checks"]["openai_key"] = {"ok": False, "source": None, "error": "openai.local.json 不是有效的 JSON（第 2 行第 14 字）"}
    broken = DashboardState(port=8080, started=0.0, phase="運作中", overview=_overview(health=health), overview_ok=True)
    text = "\n".join(_plain(render_lines(broken, 200, 25, 1.0, "20:00:00")))
    assert "金鑰設定檔格式錯誤（openai.local.json 不是有效的 JSON（第 2 行第 14 字））" in text


def test_render_fits_small_window() -> None:
    state = DashboardState(port=8080, started=0.0, phase="運作中", overview=_overview(), overview_ok=True)
    for index in range(50):
        state.events.append(("20:00:00", "INFO", f"事件 {index}"))
    lines = render_lines(state, 40, 12, 1.0, "20:00:00")
    assert len(lines) <= 12
    assert all(display_width(line) < 40 for line in _plain(lines))


def test_summarize_record_skips_traceback_noise() -> None:
    base = {"ts": "2026-10-28T12:00:00.000+00:00", "level": "WARNING", "event": "system.child_output", "ids": {"child": "web"}}
    assert summarize_record({**base, "message": "Traceback (most recent call last):"}) is None
    assert summarize_record({**base, "message": '  File "x.py", line 1, in f'}) is None
    assert summarize_record({**base, "message": "    ^^^^^^^^"}) is None
    kept = summarize_record({**base, "message": "json.decoder.JSONDecodeError: Expecting value"})
    assert kept is not None and kept[1] == "WARNING" and kept[2] == "web：json.decoder.JSONDecodeError: Expecting value"
    info = summarize_record({"ts": "bad", "level": "INFO", "event": "session.start", "message": "開始場次"})
    assert info is not None and info[2] == "開始場次"


def test_log_sink_passes_records_to_listener(tmp_path: Path) -> None:
    from backend.logsink.server import LogSinkServer

    received: list[dict] = []
    sink = LogSinkServer("127.0.0.1", 0, tmp_path / "runtime.txt", listener=received.append)
    host, port = sink.start()
    try:
        with socket.create_connection((host, port)) as connection:
            connection.sendall('{"level": "INFO", "event": "session.start", "message": "開始場次"}\n'.encode("utf-8"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not received:
            time.sleep(0.05)
    finally:
        sink.stop()
    assert received and received[0]["event"] == "session.start"
    assert "開始場次" in (tmp_path / "runtime.txt").read_text(encoding="utf-8")
