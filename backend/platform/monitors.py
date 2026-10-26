"""Windows 螢幕列舉。

以 EnumDisplayMonitors / GetMonitorInfoW 取得各螢幕在延伸桌面中的位置與大小（實體像素），
依 x 座標由左到右排序並編號；非 Windows 時回傳空清單。
"""

from __future__ import annotations

import sys
from typing import Any

_MONITORINFOF_PRIMARY = 1
_dpi_awareness_set = False


def sort_monitors(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """依 (x, y) 由左到右排序並重新編號 index。"""
    ordered = sorted(raw, key=lambda item: (item["x"], item["y"]))
    return [{**item, "index": index} for index, item in enumerate(ordered)]


def _enable_dpi_awareness() -> None:
    """讓座標以實體像素回報；每個程序只設定一次，失敗則沿用系統預設。"""
    global _dpi_awareness_set
    if _dpi_awareness_set:
        return
    _dpi_awareness_set = True
    import ctypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def _enumerate_windows() -> list[dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        monitor_enum_proc,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    found: list[dict[str, Any]] = []

    def _callback(hmonitor, _hdc, _rect, _data) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rect = info.rcMonitor
            found.append(
                {
                    "name": info.szDevice,
                    "x": int(rect.left),
                    "y": int(rect.top),
                    "width": int(rect.right - rect.left),
                    "height": int(rect.bottom - rect.top),
                    "primary": bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                }
            )
        return True

    user32.EnumDisplayMonitors(None, None, monitor_enum_proc(_callback), 0)
    return found


def list_monitors() -> list[dict[str, Any]]:
    """回傳 [{"index", "name", "x", "y", "width", "height", "primary"}]，依 x 座標由左到右排序。"""
    if sys.platform != "win32":
        return []
    _enable_dpi_awareness()
    try:
        return sort_monitors(_enumerate_windows())
    except OSError:
        return []
