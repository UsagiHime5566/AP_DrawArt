"""啟動器的純函式與小型元件（不啟動子程序、不開啟任何視窗）。"""

from __future__ import annotations

import argparse
import socket
from pathlib import Path

import pytest

from launcher import browser
from launcher.health import frontend_missing, is_ready, pending_requests
from launcher.processes import child_env, restart_delay, resolve_child_python
from launcher.single_instance import SingleInstance, instance_name
from launcher.status_page import render_status_html, write_status_page

MONITORS = [
    {"index": 0, "name": "L", "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True},
    {"index": 1, "name": "C", "x": 1920, "y": 0, "width": 1920, "height": 1080, "primary": False},
    {"index": 2, "name": "R", "x": 3840, "y": 0, "width": 1920, "height": 1080, "primary": False},
]


# ---- 瀏覽器 ----


def test_browser_command_chrome_with_monitor() -> None:
    profile = Path(r"C:\展覽 資料\data\browser-profiles\ai")
    command = browser.browser_command(
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        "http://127.0.0.1:8080/display/ai",
        profile,
        MONITORS[1],
    )
    assert command[0].endswith("chrome.exe")
    assert command[1] == f"--user-data-dir={profile}"
    assert "--kiosk" in command
    assert "--window-position=1920,0" in command
    assert "--window-size=1920,1080" in command
    assert "--edge-kiosk-type=fullscreen" not in command
    assert command[-2:] == ["--new-window", "http://127.0.0.1:8080/display/ai"]


def test_browser_command_edge_without_monitor() -> None:
    command = browser.browser_command(
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        "file:///C:/status.html",
        Path("profile"),
        None,
    )
    assert "--edge-kiosk-type=fullscreen" in command
    assert not any(arg.startswith("--window-position") for arg in command)
    assert command[-1] == "file:///C:/status.html"


def test_find_browser_auto_order(tmp_path: Path) -> None:
    env = {
        "ProgramFiles": r"C:\PF",
        "ProgramFiles(x86)": r"C:\PF86",
        "LOCALAPPDATA": r"C:\Local",
    }
    edge = Path(r"C:\PF86") / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    chrome = Path(r"C:\PF") / "Google" / "Chrome" / "Application" / "chrome.exe"
    bundled = tmp_path / "runtime" / "display-browser" / "chrome.exe"

    assert browser.find_browser("auto", tmp_path, lambda p: p in {edge, chrome}, env) == edge
    assert browser.find_browser("auto", tmp_path, lambda p: p == chrome, env) == chrome
    assert browser.find_browser("auto", tmp_path, lambda p: p in {edge, bundled}, env) == bundled
    assert browser.find_browser("auto", tmp_path, lambda p: False, env) is None


def test_find_browser_explicit_path() -> None:
    target = Path(r"D:\Browser\chrome.exe")
    assert browser.find_browser(str(target), Path("."), lambda p: p == target, {}) == target
    assert browser.find_browser(str(target), Path("."), lambda p: False, {}) is None


def test_assign_monitors_default_mapping() -> None:
    config = {"visitor": {"monitor": 0}, "ai": {"monitor": 1}, "artist": {"monitor": 2}}
    assigned, warnings = browser.assign_monitors(config, MONITORS)
    assert {role: item["name"] for role, item in assigned.items()} == {"visitor": "L", "ai": "C", "artist": "R"}
    assert warnings == []


def test_assign_monitors_custom_and_fallback() -> None:
    config = {"visitor": {"monitor": 2}, "ai": {"monitor": 0}, "artist": {"monitor": 9}}
    assigned, warnings = browser.assign_monitors(config, MONITORS)
    assert assigned["visitor"]["name"] == "R"
    assert assigned["ai"]["name"] == "L"
    assert assigned["artist"]["name"] == "R"  # 退回依角色順序的第 3 個螢幕
    assert any("artist" in warning for warning in warnings)
    assert any("同一個螢幕" in warning for warning in warnings)


def test_assign_monitors_too_few_and_none() -> None:
    assigned, warnings = browser.assign_monitors({}, MONITORS[:1])
    assert all(item["name"] == "L" for item in assigned.values())
    assert warnings

    assigned, warnings = browser.assign_monitors({}, [])
    assert assigned == {"visitor": None, "ai": None, "artist": None}
    assert len(warnings) == 3


# ---- 子程序 ----


def test_resolve_child_python(tmp_path: Path) -> None:
    pythonw = tmp_path / "pythonw.exe"
    python = tmp_path / "python.exe"
    pythonw.write_bytes(b"")
    assert resolve_child_python(pythonw) == pythonw  # 沒有 python.exe 時沿用
    python.write_bytes(b"")
    assert resolve_child_python(pythonw) == python
    assert resolve_child_python(python) == python


def test_restart_delay_backoff() -> None:
    assert [restart_delay(n) for n in range(1, 8)] == [1.0, 2.0, 5.0, 10.0, 30.0, 30.0, 30.0]
    assert restart_delay(0) == 1.0


def test_child_env(tmp_path: Path) -> None:
    env = child_env({"PYTHONPATH": "existing", "AP_DRAWART_LOG_SINK": "old"}, "127.0.0.1:9999", 8104, tmp_path)
    assert env["AP_DRAWART_LOG_SINK"] == "127.0.0.1:9999"
    assert env["AP_DRAWART_PORT"] == "8104"
    assert env["PYTHONPATH"].startswith(str(tmp_path))
    assert env["PYTHONPATH"].endswith("existing")
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "AP_DRAWART_LOG_SINK" not in child_env({"AP_DRAWART_LOG_SINK": "old"}, None, 8080, tmp_path)


# ---- 健康檢查與要求 ----


def test_pending_requests() -> None:
    assert pending_requests(None) == []
    assert pending_requests({"launcher_requests": "bad"}) == []
    health = {"launcher_requests": {"relaunch_displays": True, "shutdown": True}}
    assert pending_requests(health) == ["shutdown", "relaunch_displays"]
    assert pending_requests({"launcher_requests": {"shutdown": False, "relaunch_displays": True}}) == [
        "relaunch_displays"
    ]


def test_is_ready_and_frontend_missing() -> None:
    assert not is_ready(None)
    assert not is_ready({"checks": {"database": {"ok": False}}})
    assert is_ready({"status": "setup_required", "checks": {"database": {"ok": True}}})
    assert frontend_missing({"checks": {"frontend": {"ok": True, "missing": []}}}) == []
    assert frontend_missing({"checks": {"frontend": {"ok": False, "missing": ["draw"]}}}) == ["draw"]
    assert frontend_missing(None) == []


class _FakeHealth:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear_request(self, name: str) -> bool:
        self.cleared.append(name)
        return True


def _launcher(paths_env) -> "object":
    from launcher.main import Launcher

    args = argparse.Namespace(no_displays=True, port=8104, log_port=0, health_timeout=1.0)
    launcher = Launcher(args)
    launcher.health = _FakeHealth()
    return launcher


def test_handle_shutdown_request_clears_first(paths_env) -> None:
    launcher = _launcher(paths_env)
    launcher._handle_request("shutdown", {})
    assert launcher.health.cleared == ["shutdown"]
    assert launcher.stop_reason == "後臺要求結束展覽"


def test_handle_relaunch_request_without_displays(paths_env) -> None:
    launcher = _launcher(paths_env)
    launcher._handle_request("relaunch_displays", {"checks": {"frontend": {"ok": True}}})
    assert launcher.health.cleared == ["relaunch_displays"]
    assert launcher.stop_reason is None
    assert launcher.windows == {}  # --no-displays 時不開任何視窗


def test_port_available() -> None:
    from launcher.main import port_available

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        assert not port_available("127.0.0.1", port)


# ---- 單一執行個體與狀態頁 ----


def test_single_instance(tmp_path: Path) -> None:
    name = instance_name(tmp_path / "data")
    first = SingleInstance(name, tmp_path)
    second = SingleInstance(name, tmp_path)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_instance_name_depends_on_data_dir(tmp_path: Path) -> None:
    assert instance_name(tmp_path / "a") == instance_name(tmp_path / "a")
    assert instance_name(tmp_path / "a") != instance_name(tmp_path / "b")
    assert instance_name(tmp_path).startswith("Local\\AP_DrawArt_Launcher_")


def test_status_page_escapes_and_writes(tmp_path: Path) -> None:
    html_text = render_status_html("無法開展", "連線埠 <8080> 被占用", ["a & b"], ["http://192.168.1.10:8080/admin"])
    assert "&lt;8080&gt;" in html_text
    assert "a &amp; b" in html_text
    assert "http://192.168.1.10:8080/admin" in html_text
    url = write_status_page(tmp_path / "status.html", "標題", "訊息", [], [])
    assert url.startswith("file:///")
    assert "標題" in (tmp_path / "status.html").read_text(encoding="utf-8")


def test_launcher_uses_port_argument(paths_env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AP_DRAWART_PORT", raising=False)
    launcher = _launcher(paths_env)
    assert launcher.port == 8104
    assert launcher.base_url == "http://127.0.0.1:8104"
