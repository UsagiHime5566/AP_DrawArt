"""五秒內連按五次 Esc：啟動器層級的計數與前景判斷（不輪詢真實鍵盤）。"""

from __future__ import annotations

from launcher.escape_watch import EscapeSequence, EscapeWatcher


def test_five_presses_within_five_seconds_trigger_once() -> None:
    sequence = EscapeSequence()
    assert [sequence.press(t) for t in (0.0, 1.0, 2.0, 3.0)] == [False] * 4
    assert sequence.press(4.9) is True
    # 觸發後重新計數
    assert sequence.press(5.0) is False


def test_presses_older_than_five_seconds_do_not_count() -> None:
    sequence = EscapeSequence()
    for t in (0.0, 1.0, 2.0, 3.0):
        sequence.press(t)
    # 第 0 秒那次已超過五秒，只剩四次
    assert sequence.press(5.5) is False
    assert sequence.press(5.6) is True


def test_watcher_counts_only_when_exhibition_window_is_foreground() -> None:
    triggered: list[bool] = []
    watcher = EscapeWatcher(lambda pid: pid == 42, lambda: triggered.append(True))
    for t in range(4):
        watcher.handle_press(float(t), 42)
    # 其他視窗的 Esc 不計入
    assert watcher.handle_press(4.0, 7) is False
    assert watcher.handle_press(4.1, None) is False
    assert triggered == []
    assert watcher.handle_press(4.2, 42) is True
    assert triggered == [True]


def test_watcher_ignores_target_check_errors() -> None:
    def broken(_pid: int | None) -> bool:
        raise RuntimeError("psutil 掃描失敗")

    watcher = EscapeWatcher(broken, lambda: None)
    assert all(watcher.handle_press(float(t), 1) is False for t in range(6))
