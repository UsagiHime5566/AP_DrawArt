"""螢幕列舉：排序、編號與回傳格式（只查詢，不開視窗）。"""

from __future__ import annotations

import sys

from backend.platform.monitors import list_monitors, sort_monitors


def test_sort_monitors_left_to_right() -> None:
    raw = [
        {"name": "B", "x": 1920, "y": 0, "width": 1920, "height": 1080, "primary": False},
        {"name": "C", "x": 3840, "y": 0, "width": 1920, "height": 1080, "primary": False},
        {"name": "A", "x": -1920, "y": 0, "width": 1920, "height": 1080, "primary": False},
        {"name": "P", "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True},
    ]
    result = sort_monitors(raw)
    assert [item["name"] for item in result] == ["A", "P", "B", "C"]
    assert [item["index"] for item in result] == [0, 1, 2, 3]


def test_list_monitors_format() -> None:
    monitors = list_monitors()
    assert isinstance(monitors, list)
    if sys.platform != "win32":
        assert monitors == []
        return
    assert monitors, "Windows 上至少應偵測到一個螢幕"
    assert [item["index"] for item in monitors] == list(range(len(monitors)))
    assert [item["x"] for item in monitors] == sorted(item["x"] for item in monitors)
    for item in monitors:
        assert set(item) == {"index", "name", "x", "y", "width", "height", "primary"}
        assert item["width"] > 0 and item["height"] > 0
        assert isinstance(item["primary"], bool)
    assert sum(item["primary"] for item in monitors) == 1
