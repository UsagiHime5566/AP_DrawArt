"""標準關閉方式：展覽視窗被正常關閉即結束、stop-exhibition.bat、遺留程序清除與工作物件。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from launcher import browser
from launcher.processes import (
    KillOnCloseJob,
    find_exhibition_processes,
    launched_module,
    process_data_dir,
    stop_processes,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows 程序 API")


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


# ---- 純函式 ----


def test_launched_module() -> None:
    assert launched_module(["python.exe", "-m", "backend"]) == "backend"
    assert launched_module(["pythonw.exe", "-X", "utf8", "-m", "backend.worker"]) == "backend.worker"
    assert launched_module(["python.exe", "-m"]) is None
    assert launched_module(["msedge.exe", "--kiosk"]) is None
    assert launched_module([]) is None


def test_process_data_dir_follows_config_rules(tmp_path: Path) -> None:
    cwd = str(tmp_path / "app")
    assert process_data_dir({}, cwd) == tmp_path / "app" / "data"
    assert process_data_dir({"AP_DRAWART_ROOT": str(tmp_path)}, cwd) == tmp_path / "data"
    assert process_data_dir({"AP_DRAWART_ROOT": str(tmp_path), "AP_DRAWART_DATA_DIR": str(tmp_path / "d")}, cwd) == (
        tmp_path / "d"
    )
    # 空字串等同未設定
    assert process_data_dir({"AP_DRAWART_ROOT": "", "AP_DRAWART_DATA_DIR": ""}, cwd) == tmp_path / "app" / "data"


def test_pick_main_process_skips_edge_handoff_stub() -> None:
    # Edge：啟動器開的程序把視窗交給另一個主程序後就結束，要監看的是另一個
    assert browser.pick_main_process([10, 20], spawned_pid=10, settled=False) == 20
    assert browser.pick_main_process([20], spawned_pid=10, settled=False) == 20
    # Chrome：只有啟動器開的那一個，過了啟動緩衝期仍在才採用
    assert browser.pick_main_process([10], spawned_pid=10, settled=False) is None
    assert browser.pick_main_process([10], spawned_pid=10, settled=True) == 10
    assert browser.pick_main_process([], spawned_pid=10, settled=True) is None


# ---- 程序 API ----


def test_process_watch_reports_exit_code() -> None:
    running = _sleeper()
    finished = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    running_watch = browser.ProcessWatch(running.pid)
    finished_watch = browser.ProcessWatch(finished.pid)
    try:
        assert running_watch.exit_code() is None
        finished.wait(10)
        expected = 3 if sys.platform == "win32" else -1
        assert finished_watch.exit_code() == expected
    finally:
        running.kill()
        running.wait(10)
        running_watch.close()
        finished_watch.close()


@windows_only
def test_kill_on_close_job_ends_assigned_processes() -> None:
    job = KillOnCloseJob()
    inside = _sleeper()
    outside = _sleeper()
    try:
        assert job.assign(inside.pid)
        job.close()
        # 啟動器結束（工作物件關閉）時，加入的程序一併結束；其他程序不受影響
        assert inside.wait(10) is not None
        assert outside.poll() is None
    finally:
        for process in (inside, outside):
            if process.poll() is None:
                process.kill()
                process.wait(10)


def test_find_and_stop_leftover_children(tmp_path: Path) -> None:
    # 以同名模組模擬上次遺留的 `python -m backend.worker`，不啟動真正的 Worker
    package = tmp_path / "code" / "backend"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "worker.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("AP_DRAWART_")}
    env["PYTHONPATH"] = str(tmp_path / "code")
    env["AP_DRAWART_ROOT"] = str(tmp_path)
    leftover = subprocess.Popen([sys.executable, "-m", "backend.worker"], cwd=tmp_path / "code", env=env)
    try:
        deadline = time.monotonic() + 10
        found: list = []
        while time.monotonic() < deadline and not found:
            found = find_exhibition_processes(tmp_path / "data")
            time.sleep(0.2)
        assert [process.pid for process in found] == [leftover.pid]
        # 其他資料目錄的程序不會被當成遺留
        assert find_exhibition_processes(tmp_path / "other-data") == []
        stop_processes(found)
        assert leftover.wait(10) is not None
    finally:
        if leftover.poll() is None:
            leftover.kill()
            leftover.wait(10)


# ---- 啟動器流程 ----


class _FakeWatch:
    def __init__(self, pid: int, code: int | None) -> None:
        self.pid = pid
        self.code = code
        self.closed = False

    def exit_code(self) -> int | None:
        return self.code

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def _launcher(paths_env, **overrides):
    from launcher.main import Launcher

    values = {
        "no_displays": True, "port": 8105, "log_port": 0, "health_timeout": 1.0,
        "stop": False, "stop_timeout": 5.0,
    }
    values.update(overrides)
    return Launcher(argparse.Namespace(**values))


def _live_launcher(paths_env, monkeypatch: pytest.MonkeyPatch, main_pids: list[int], code: int | None):
    """有一個已開啟 10 秒的展覽視窗；瀏覽器主程序與結束代碼由參數決定。"""
    from launcher.main import DisplayWindow

    launcher = _launcher(paths_env, no_displays=False)
    launcher.display_mode = "live"
    launcher.windows["exhibition"] = DisplayWindow(
        "exhibition", paths_env.data_dir / "browser-profiles" / "exhibition", None,
        launched_at=time.monotonic() - 10, spawned_pid=1,
    )
    launched: list[str] = []
    monkeypatch.setattr(browser, "find_profile_processes", lambda _dir: [_FakeProcess(pid) for pid in main_pids])
    monkeypatch.setattr(browser, "ProcessWatch", lambda pid: _FakeWatch(pid, code))
    monkeypatch.setattr(launcher, "_display_targets", lambda: (Path("browser.exe"), {}))
    monkeypatch.setattr(launcher, "_launch_window", lambda _path, window: launched.append(window.role))
    return launcher, launched


def test_window_closed_normally_stops_exhibition(paths_env, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher, launched = _live_launcher(paths_env, monkeypatch, [2], code=0)
    launcher._ensure_windows(time.monotonic())
    assert launcher.stop_reason == "展覽視窗被關閉"
    assert launched == []


def test_window_crash_is_reopened(paths_env, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher, launched = _live_launcher(paths_env, monkeypatch, [2], code=15)
    launcher._ensure_windows(time.monotonic())
    assert launcher.stop_reason is None
    assert launched == ["exhibition"]


def test_running_window_is_left_alone_and_counts_as_foreground(paths_env, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher, launched = _live_launcher(paths_env, monkeypatch, [2], code=None)
    launcher._ensure_windows(time.monotonic())
    assert launcher.stop_reason is None and launched == []
    # Esc 備援只在前景視窗屬於被監看的主程序時計數
    assert launcher._is_exhibition_foreground(2)
    assert not launcher._is_exhibition_foreground(1)


def test_missing_window_is_reopened_only_after_grace(paths_env, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher, launched = _live_launcher(paths_env, monkeypatch, [], code=None)
    window = launcher.windows["exhibition"]
    window.launched_at = time.monotonic()
    launcher._ensure_windows(time.monotonic())
    assert launched == []
    window.launched_at = time.monotonic() - 10
    launcher._ensure_windows(time.monotonic())
    assert launched == ["exhibition"]
    assert launcher.stop_reason is None


def test_stop_file_request(paths_env) -> None:
    launcher = _launcher(paths_env)
    assert launcher._stop_requested() is False
    launcher.stop_file.write_text("1", encoding="utf-8")
    assert launcher._stop_requested() is True
    assert launcher.stop_reason == "stop-exhibition.bat 要求結束展覽"
    assert not launcher.stop_file.exists()


def test_stop_without_running_launcher(paths_env, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import launcher.main as main

    monkeypatch.setattr(main, "find_exhibition_processes", lambda _data_dir, *_: [])
    monkeypatch.setattr(browser, "kill_profile_processes", lambda _dir: 0)
    assert _launcher(paths_env, stop=True).run() == 0
    assert "沒有在執行" in capsys.readouterr().out


def test_stop_waits_for_running_launcher(paths_env) -> None:
    from launcher.single_instance import SingleInstance, instance_name

    running = SingleInstance(instance_name(paths_env.data_dir), paths_env.tmp_dir)
    assert running.acquire()
    stopper = _launcher(paths_env, stop=True)
    stopper.instance = SingleInstance(running.name, paths_env.tmp_dir)

    def fake_running_launcher() -> None:
        # 執行中的啟動器：看到結束要求就收尾並釋放執行鎖
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not stopper.stop_file.exists():
            time.sleep(0.05)
        stopper.stop_file.unlink()
        running.release()

    thread = threading.Thread(target=fake_running_launcher)
    thread.start()
    try:
        assert stopper.run() == 0
    finally:
        thread.join(10)
        running.release()
