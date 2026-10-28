"""啟動器主控台畫面：後臺網址、即時狀態與最近事件。

start-exhibition.bat 以有主控台的 python.exe 執行啟動器，這個視窗就是現場人員看狀態的地方。
畫面在背景執行緒每秒以 ANSI 控制碼原地重畫（不捲動）；輸出被導向或主控台不支援時不顯示。
重畫與主迴圈分開：有人在視窗內選取文字（QuickEdit）暫停輸出時，只有畫面停住，展覽照常監看。
關閉這個視窗等同結束展覽（見 install_close_handler）。
"""

from __future__ import annotations

import collections
import shutil
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.timeutil import parse_iso

TITLE = "人工對畫(話) 展覽主機"
FOOTER = "結束展覽：展覽視窗按 Alt+F4、雙擊 stop-exhibition.bat，或關閉這個視窗。"
_REFRESH_SECONDS = 1.0
_OVERVIEW_SECONDS = 2.0
_EVENT_LIMIT = 200

_MODES = {"gallery": "一般輪播", "drawing": "觀眾作畫中", "personal": "本人首播"}
_STAGES = {"visitor": "觀眾作品", "ai": "AI 作品", "artist": "藝術家原圖"}
_KINDS = {"premiere": "首播", "history": "重播", "personal": "本人"}
_CHECKS = {
    "database": "資料庫", "frontend": "前端", "artwork": "原圖", "worker": "AI Worker",
    "displays": "展覽螢幕", "disk": "磁碟", "log": "日誌", "openai_key": "OpenAI 金鑰",
}

_RESET = "\x1b[0m"
_COLORS = {"WARNING": "\x1b[33m", "ERROR": "\x1b[31m", "OK": "\x1b[32m", "DIM": "\x1b[90m", "TITLE": "\x1b[1m"}

# Windows 主控台控制事件：關閉視窗、登出、關機
_CTRL_CLOSE_EVENTS = (2, 5, 6)


# ---- 寬度與截斷 ----


def display_width(text: str) -> int:
    """主控台顯示寬度；全形與「寬度不定」字元都算 2 欄（中文主控台會以 2 欄顯示）。"""
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F", "A") else 1
    return width


def fit(text: str, width: int) -> str:
    """截到不超過 width 欄，超出時以 ... 結尾。"""
    if display_width(text) <= width:
        return text
    limit = max(width - 3, 0)
    kept, used = [], 0
    for char in text:
        char_width = display_width(char)
        if used + char_width > limit:
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + "..."[: max(width - used, 0)]


# ---- 事件 ----


def summarize_record(record: dict[str, Any]) -> tuple[str, str, str] | None:
    """日誌事件轉成 (時間, 等級, 文字)；子程序例外堆疊的中間行略過，只留最後的錯誤說明。"""
    message = str(record.get("message", ""))
    if record.get("event") == "system.child_output":
        stripped = message.strip()
        if message[:1] in (" ", "\t") or stripped.startswith("Traceback") or not stripped.strip("^~ "):
            return None
        child = (record.get("ids") or {}).get("child") or "子程序"
        message = f"{child}：{stripped}"
    try:
        clock = parse_iso(str(record.get("ts"))).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        clock = datetime.now().strftime("%H:%M:%S")
    return clock, str(record.get("level") or "INFO"), message.replace("\n", " ")


# ---- 畫面內容 ----


@dataclass
class DashboardState:
    port: int
    started: float
    phase: str = "啟動中"
    overview: dict[str, Any] | None = None
    overview_ok: bool = False
    events: collections.deque = field(default_factory=lambda: collections.deque(maxlen=_EVENT_LIMIT))


def _format_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _uptime(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _status_lines(state: DashboardState) -> list[tuple[str, str | None]]:
    """(文字, 顏色) 列表；顏色為 _COLORS 的鍵或 None。"""
    overview = state.overview
    if overview is None:
        return [(f"系統    {state.phase}", "WARNING" if state.phase != "啟動中" else None)]

    health = overview.get("health") or {}
    checks = health.get("checks") or {}
    status = health.get("status")
    if not state.overview_ok:
        lines = [("系統    Web 服務暫時沒有回應，持續重試（以下為最後取得的狀態）", "WARNING")]
    elif status == "ok":
        lines = [("系統    正常", "OK")]
    elif status == "setup_required":
        key_error = (checks.get("openai_key") or {}).get("error")
        detail = f"金鑰設定檔格式錯誤（{key_error}）" if key_error else "尚未設定 OpenAI 金鑰"
        lines = [(f"系統    需要設定：{detail}，請到後臺設定", "ERROR")]
    else:
        failing = [_CHECKS.get(name, name) for name, check in checks.items() if not (check or {}).get("ok")]
        lines = [(f"系統    部分異常：{'、'.join(failing) or '—'}", "WARNING")]
    if state.phase not in ("運作中", "啟動中"):
        lines.append((f"        {state.phase}", "WARNING"))

    view = overview.get("playback") or {}
    mode = _MODES.get(view.get("mode"), view.get("mode") or "—")
    parts = [mode]
    if view.get("mode") != "drawing" and view.get("stage"):
        detail = _STAGES.get(view["stage"], view["stage"])
        kind = _KINDS.get(view.get("kind"))
        if kind:
            detail = f"{kind}{detail}"
        if view.get("receipt_order") is not None:
            detail += f" #{view['receipt_order']}"
        parts.append(detail)
    if view.get("waiting_for_ai"):
        parts.append("等待 AI 結果")
    intake = "開放" if overview.get("intake_enabled") else "暫停"
    lines.append((f"展覽    {' / '.join(parts)}    接待：{intake}", None if overview.get("intake_enabled") else "WARNING"))

    displays = checks.get("displays") or {}
    lines.append(
        ("螢幕    展覽視窗已連線", None) if displays.get("ok") else ("螢幕    展覽視窗未連線", "WARNING")
    )
    counts = overview.get("counts") or {}
    worker_ok = (checks.get("worker") or {}).get("ok")
    lines.append((
        f"AI      Worker {'正常' if worker_ok else '沒有回應'}，排隊 {counts.get('ai_queued', 0)} 件、"
        f"生成中 {counts.get('ai_running', 0)} 件",
        None if worker_ok else "WARNING",
    ))
    lines.append((
        f"作品    已收 {counts.get('submissions_total', 0)} 件，已播 {counts.get('played_total', 0)} 件，"
        f"未播 {counts.get('playback_unplayed', 0)} 件",
        None,
    ))
    disk = checks.get("disk") or {}
    if "free_bytes" in disk:
        ratio = disk.get("free_ratio") or 0
        text = f"磁碟    可用 {_format_bytes(disk['free_bytes'])}（{ratio * 100:.1f}%）"
        if disk.get("discarding"):
            lines.append((text + "，空間不足，新作品會被捨棄", "ERROR"))
        else:
            lines.append((text, None))
    return lines


def render_lines(state: DashboardState, width: int, height: int, now: float, clock: str) -> list[str]:
    """組出整個畫面（已上色、已截斷），行數不超過 height。"""
    usable = max(width - 1, 20)
    rows: list[tuple[str, str | None]] = []
    right = f"運行 {_uptime(now - state.started)}  {clock}"
    gap = usable - display_width(TITLE) - display_width(right)
    rows.append((TITLE + " " * max(gap, 2) + right, "TITLE"))
    rows.append(("=" * usable, "DIM"))

    rows.append(("後臺網址（同一網路的手機、平板或電腦開啟）", None))
    urls = ((state.overview or {}).get("health") or {}).get("admin_urls") or []
    rows.extend((f"  {url}", "OK") for url in urls)
    if not urls:
        rows.append(("  等待 Web 服務就緒後顯示", "DIM"))
    rows.append((f"  本機：http://127.0.0.1:{state.port}/admin", "DIM"))
    rows.append(("", None))
    rows.extend(_status_lines(state))
    rows.append(("-" * usable, "DIM"))
    rows.append(("最近事件", None))

    footer = [("-" * usable, "DIM"), (FOOTER, "DIM")]
    room = height - len(rows) - len(footer)
    events = list(state.events)[-room:] if room > 0 else []
    for clock_text, level, message in events:
        mark = {"WARNING": "警告", "ERROR": "錯誤"}.get(level, "    ")
        rows.append((f"{clock_text} {mark} {message}", level if level in ("WARNING", "ERROR") else None))
    rows.extend((("", None),) * max(room - len(events), 0))
    rows.extend(footer)

    lines = []
    for text, color in rows[:height]:
        text = fit(text, usable)
        lines.append(f"{_COLORS[color]}{text}{_RESET}" if color else text)
    return lines


# ---- 主控台 ----


def prepare_console(title: str = TITLE) -> bool:
    """輸出是互動主控台時開啟 ANSI 控制碼並設定視窗標題；不適用時回傳 False。"""
    stdout = sys.stdout
    if stdout is None or not stdout.isatty():
        return False
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    handle = kernel32.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
    if not kernel32.SetConsoleMode(handle, mode.value | 0x0001 | 0x0004):
        return False
    kernel32.SetConsoleTitleW(title)
    return True


def install_close_handler(on_close: Callable[[], None]) -> Any:
    """主控台視窗被關閉（或登出、關機）時呼叫 on_close；回傳值須保留，避免回呼被回收。

    系統送出這些事件後約 5 秒就會強制結束程序，on_close 應在時限內完成收尾。
    Ctrl+C 不在此處理，維持 KeyboardInterrupt。
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def handler(ctrl_type: int) -> bool:
        if ctrl_type in _CTRL_CLOSE_EVENTS:
            on_close()
            return True
        return False

    callback = handler_type(handler)
    if not ctypes.WinDLL("kernel32").SetConsoleCtrlHandler(callback, True):
        return None
    return callback


class ConsoleDashboard:
    """背景執行緒定時取得後臺總覽並重畫主控台。"""

    def __init__(self, port: int, fetch_overview: Callable[[], dict[str, Any] | None]) -> None:
        self.state = DashboardState(port=port, started=time.monotonic())
        self._fetch = fetch_overview
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="console-dashboard", daemon=True)

    def start(self) -> None:
        self._write("\x1b[?25l\x1b[2J")
        self._thread.start()

    def stop(self) -> None:
        """停止重畫並把游標移到畫面下方，之後的文字接著顯示。"""
        if self._stop.is_set():
            return
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2.0)
        self._draw()
        self._write("\x1b[?25h\n")

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.state.phase = phase

    def add_record(self, record: dict[str, Any]) -> None:
        """日誌寫入器的 listener（在寫入執行緒呼叫）。"""
        summary = summarize_record(record)
        if summary is not None:
            with self._lock:
                self.state.events.append(summary)

    def _run(self) -> None:
        next_fetch = 0.0
        while not self._stop.is_set():
            if time.monotonic() >= next_fetch:
                overview = self._fetch()
                with self._lock:
                    self.state.overview_ok = overview is not None
                    if overview is not None:
                        self.state.overview = overview
                next_fetch = time.monotonic() + _OVERVIEW_SECONDS
            if not self._draw():
                return
            self._stop.wait(_REFRESH_SECONDS)

    def _draw(self) -> bool:
        size = shutil.get_terminal_size((100, 30))
        with self._lock:
            lines = render_lines(
                self.state, size.columns, size.lines, time.monotonic(), datetime.now().strftime("%H:%M:%S")
            )
        frame = "\x1b[H" + "\n".join(line + "\x1b[K" for line in lines) + "\x1b[J"
        return self._write(frame)

    @staticmethod
    def _write(text: str) -> bool:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
            return True
        except (OSError, ValueError, AttributeError):
            # 主控台已關閉或輸出失效，停止重畫即可
            return False
