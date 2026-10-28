"""展示瀏覽器：尋找執行檔、組裝 kiosk 指令、對應角色與螢幕、管理視窗程序。

每個角色使用專用的 --user-data-dir，視窗程序以此辨識；
只結束使用這些專用設定檔目錄的程序，不影響工作人員自己開的瀏覽器。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from backend.config import DISPLAY_ROLES

_KIOSK_FLAGS = (
    "--kiosk",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-infobars",
    "--noerrdialogs",
    "--disable-translate",
    "--disable-features=Translate,TranslateUI",
    "--autoplay-policy=no-user-gesture-required",
    "--overscroll-history-navigation=0",
    "--disable-pinch",
)


def browser_candidates(root: Path, env: Mapping[str, str]) -> list[Path]:
    """auto 模式的搜尋順序：交付包內瀏覽器 → Edge → Chrome。"""
    bundled = root / "runtime" / "display-browser"
    program_files = env.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidates = [
        bundled / "chrome.exe",
        bundled / "msedge.exe",
        bundled / "chrome-win" / "chrome.exe",
        Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return candidates


def find_browser(
    setting: str | None,
    root: Path,
    exists: Callable[[Path], bool] = Path.is_file,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """依 displays.json 的 browser 設定找執行檔；指定路徑不存在時回傳 None。"""
    if setting and setting != "auto":
        path = Path(setting)
        return path if exists(path) else None
    for candidate in browser_candidates(root, env if env is not None else os.environ):
        if exists(candidate):
            return candidate
    return None


def assign_monitors(
    config: Mapping[str, Any], monitors: list[dict[str, Any]],
    roles: tuple[str, ...] = DISPLAY_ROLES,
) -> tuple[dict[str, dict[str, Any] | None], list[str]]:
    """依設定把左／中／右角色對應到螢幕；設定的螢幕不存在時依角色順序退而求其次。"""
    assigned: dict[str, dict[str, Any] | None] = {}
    warnings: list[str] = []
    for default_index, role in enumerate(roles):
        role_config = config.get(role) or {}
        wanted = role_config.get("monitor", default_index) if isinstance(role_config, Mapping) else default_index
        monitor = next((item for item in monitors if item.get("index") == wanted), None)
        if monitor is None:
            if monitors:
                monitor = monitors[min(default_index, len(monitors) - 1)]
                warnings.append(f"{role} 指定的螢幕 {wanted} 不存在，改用螢幕 {monitor['index']}")
            else:
                warnings.append(f"{role} 找不到任何螢幕，視窗將使用預設位置")
        assigned[role] = monitor

    used = [monitor["index"] for monitor in assigned.values() if monitor is not None]
    if len(set(used)) < len(used):
        warnings.append("多個角色對應到同一個螢幕，畫面會互相覆蓋")
    return assigned, warnings


def profile_arg(profile_dir: Path) -> str:
    return f"--user-data-dir={profile_dir}"


def browser_command(
    browser: Path, url: str, profile_dir: Path, monitor: Mapping[str, Any] | None
) -> list[str]:
    command = [str(browser), profile_arg(profile_dir), *_KIOSK_FLAGS]
    if browser.name.lower() == "msedge.exe":
        command.append("--edge-kiosk-type=fullscreen")
    if monitor is not None:
        command.append(f"--window-position={monitor['x']},{monitor['y']}")
        command.append(f"--window-size={monitor['width']},{monitor['height']}")
    command += ["--new-window", url]
    return command


def _same_arg(left: str, right: str) -> bool:
    return os.path.normcase(left) == os.path.normcase(right)


def find_profile_processes(profile_dir: Path, main_only: bool = True) -> list[Any]:
    """找出使用指定專用設定檔目錄的瀏覽器程序；main_only 時排除 --type= 子程序。"""
    import psutil

    marker = profile_arg(profile_dir)
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = process.info.get("cmdline") or []
        except psutil.Error:
            continue
        if not any(_same_arg(arg, marker) for arg in cmdline):
            continue
        if main_only and any(arg.startswith("--type=") for arg in cmdline):
            continue
        found.append(process)
    return found


def kill_profile_processes(profile_dir: Path, timeout: float = 5.0) -> int:
    """結束使用專用設定檔目錄的所有程序（含子程序），回傳結束的程序數。"""
    import psutil

    targets: dict[int, Any] = {}
    for process in find_profile_processes(profile_dir, main_only=False):
        targets[process.pid] = process
        try:
            for child in process.children(recursive=True):
                targets[child.pid] = child
        except psutil.Error:
            pass
    for process in targets.values():
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(list(targets.values()), timeout=timeout)
    return len(targets)


def open_window(command: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
