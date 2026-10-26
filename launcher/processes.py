"""Web 與 Worker 子程序的啟動、輸出轉送、重啟與停止。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.logclient import RuntimeLogger

_RESTART_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
# 連續正常運作超過此秒數，重啟退避歸零
_STABLE_SECONDS = 60.0


def restart_delay(consecutive_failures: int) -> float:
    """第 n 次連續異常結束後，等待多久再重啟。"""
    return _RESTART_DELAYS[min(max(consecutive_failures, 1), len(_RESTART_DELAYS)) - 1]


def resolve_child_python(executable: Path) -> Path:
    """子程序需要能輸出錯誤訊息，啟動器以 pythonw 執行時改用同目錄的 python.exe。"""
    if executable.name.lower() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            return console
    return executable


def console_attached() -> bool:
    """啟動器本身是否有主控台（開發時以 python.exe 執行）。"""
    if sys.platform != "win32":
        return sys.stdout is not None
    import ctypes

    return bool(ctypes.WinDLL("kernel32").GetConsoleWindow())


def child_creationflags(has_console: bool) -> int:
    """有主控台時共用並自成程序群組（可送 CTRL_BREAK 正常結束）；否則不顯示任何主控台視窗。"""
    if sys.platform != "win32":
        return 0
    if has_console:
        return subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


@dataclass
class ChildSpec:
    name: str
    args: list[str]
    env: dict[str, str]
    cwd: Path


@dataclass
class ManagedChild:
    spec: ChildSpec
    log: RuntimeLogger
    has_console: bool
    process: subprocess.Popen | None = None
    started_at: float = 0.0
    consecutive_failures: int = 0
    restart_at: float | None = None
    total_restarts: int = 0
    _reader: threading.Thread | None = field(default=None, repr=False)

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.spec.args,
            cwd=self.spec.cwd,
            env=self.spec.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=child_creationflags(self.has_console),
        )
        self.started_at = time.monotonic()
        self.restart_at = None
        self._reader = threading.Thread(
            target=self._forward_output, name=f"{self.spec.name}-output", daemon=True
        )
        self._reader.start()

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    def poll(self) -> int | None:
        return self.process.poll() if self.process is not None else None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def on_exit(self, now: float) -> float:
        """記錄異常結束並排定重啟時間，回傳等待秒數。"""
        if now - self.started_at >= _STABLE_SECONDS:
            self.consecutive_failures = 0
        self.consecutive_failures += 1
        delay = restart_delay(self.consecutive_failures)
        self.restart_at = now + delay
        return delay

    def stop(self, graceful_timeout: float = 8.0) -> int | None:
        process = self.process
        if process is None or process.poll() is not None:
            return process.poll() if process is not None else None
        if self.has_console and sys.platform == "win32":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                return process.wait(graceful_timeout)
            except (OSError, subprocess.TimeoutExpired):
                pass
        process.terminate()
        try:
            return process.wait(3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait(3.0)

    def _forward_output(self) -> None:
        """子程序的主控台輸出（例如未捕捉的例外）轉入集中日誌。"""
        process = self.process
        if process is None or process.stdout is None:
            return
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self.log.warning("system.child_output", line[:2000], child=self.spec.name, pid=process.pid)


def child_env(base: dict[str, str], log_sink: str | None, port: int, code_root: Path) -> dict[str, str]:
    env = dict(base)
    if log_sink:
        env["AP_DRAWART_LOG_SINK"] = log_sink
    else:
        env.pop("AP_DRAWART_LOG_SINK", None)
    env["AP_DRAWART_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(code_root) + (os.pathsep + existing if existing else "")
    return env
