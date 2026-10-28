"""五秒內連按五次 Esc 結束展覽：啟動器層級的備援偵測。

展覽頁本身也會偵測 Esc 並要求結束（可先保存草稿、顯示結束畫面）。這裡另以
GetAsyncKeyState 輪詢鍵盤，只在前景視窗屬於展覽瀏覽器時計數；即使 Web 服務
沒有回應，現場人員仍能關閉展覽。其他視窗中的 Esc 不計入。
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

ESCAPE_PRESSES = 5
ESCAPE_WINDOW_SECONDS = 5.0

_VK_ESCAPE = 0x1B
_POLL_SECONDS = 0.02


class EscapeSequence:
    """只計算最近 window 秒內的獨立按下（長按不會重複計數）。"""

    def __init__(self, presses: int = ESCAPE_PRESSES, window: float = ESCAPE_WINDOW_SECONDS) -> None:
        self.presses = presses
        self.window = window
        self._times: list[float] = []

    def press(self, now: float) -> bool:
        self._times = [pressed for pressed in self._times if now - pressed <= self.window]
        self._times.append(now)
        if len(self._times) < self.presses:
            return False
        self._times.clear()
        return True


def foreground_pid() -> int | None:
    """目前前景視窗所屬的程序；非 Windows 或沒有前景視窗時回傳 None。"""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


class EscapeWatcher:
    """背景執行緒輪詢 Esc；is_target(前景程序) 為真時才計數，湊滿五次呼叫 on_trigger。"""

    def __init__(
        self,
        is_target: Callable[[int | None], bool],
        on_trigger: Callable[[], None],
        sequence: EscapeSequence | None = None,
    ) -> None:
        self.is_target = is_target
        self.on_trigger = on_trigger
        self.sequence = sequence or EscapeSequence()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="escape-watch", daemon=True)

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def handle_press(self, now: float, pid: int | None) -> bool:
        """處理一次按下；回傳是否已湊滿並觸發。"""
        try:
            counted = self.is_target(pid)
        except Exception:  # noqa: BLE001 - 判斷失敗時不計數，不影響展覽
            counted = False
        if counted and self.sequence.press(now):
            self.on_trigger()
            return True
        return False

    def _run(self) -> None:
        import ctypes

        user32 = ctypes.WinDLL("user32")
        user32.GetAsyncKeyState.restype = ctypes.c_short
        was_down = False
        while not self._stop.wait(_POLL_SECONDS):
            down = bool(user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000)
            if down and not was_down:
                self.handle_press(time.monotonic(), foreground_pid())
            was_down = down
