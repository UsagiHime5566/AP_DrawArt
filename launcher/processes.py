"""Web 與 Worker 子程序的啟動、輸出轉送、重啟與停止，以及上次遺留程序的辨識。"""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.logclient import RuntimeLogger

_RESTART_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
# 連續正常運作超過此秒數，重啟退避歸零
_STABLE_SECONDS = 60.0

CHILD_MODULES = ("backend", "backend.worker")

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100


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
    job: KillOnCloseJob | None = None
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
        if self.job is not None and not self.job.assign(self.process.pid):
            self.log.warning(
                "system.child_job", f"{self.spec.name} 無法加入工作物件，啟動器被強制結束時可能殘留",
                child=self.spec.name,
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


class KillOnCloseJob:
    """Windows 工作物件：啟動器一結束（包括被強制結束），系統就一併結束加入的程序。

    避免啟動器被關掉後 Web／Worker 留在背景占用連線埠與資料庫，導致無法再次開展。
    其他平台不做任何事。
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        if sys.platform != "win32":
            return
        import ctypes

        kernel32 = _job_kernel32()
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW 失敗")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject 失敗")
        self._handle = handle

    def assign(self, pid: int) -> bool:
        """把程序加入工作物件；失敗時回傳 False（例如程序已結束）。"""
        if self._handle is None:
            return False
        kernel32 = _job_kernel32()
        process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(self._handle, process))
        finally:
            kernel32.CloseHandle(process)

    def close(self) -> None:
        """關閉工作物件：仍在其中的程序會被結束。"""
        if self._handle is not None:
            _job_kernel32().CloseHandle(self._handle)
            self._handle = None


if sys.platform == "win32":
    import ctypes as _ctypes
    from ctypes import wintypes as _wintypes

    class _BasicLimitInformation(_ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", _wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", _wintypes.LARGE_INTEGER),
            ("LimitFlags", _wintypes.DWORD),
            ("MinimumWorkingSetSize", _ctypes.c_size_t),
            ("MaximumWorkingSetSize", _ctypes.c_size_t),
            ("ActiveProcessLimit", _wintypes.DWORD),
            ("Affinity", _ctypes.c_size_t),
            ("PriorityClass", _wintypes.DWORD),
            ("SchedulingClass", _wintypes.DWORD),
        ]

    class _IoCounters(_ctypes.Structure):
        _fields_ = [
            (name, _ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )
        ]

    class _ExtendedLimitInformation(_ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", _ctypes.c_size_t),
            ("JobMemoryLimit", _ctypes.c_size_t),
            ("PeakProcessMemoryUsed", _ctypes.c_size_t),
            ("PeakJobMemoryUsed", _ctypes.c_size_t),
        ]


@functools.cache
def _job_kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


# ---- 上次未正常結束遺留的程序 ----


def launched_module(cmdline: list[str]) -> str | None:
    """`python -m <模組>` 形式的命令列回傳模組名稱，其他回傳 None。"""
    for index, arg in enumerate(cmdline[:-1]):
        if arg == "-m":
            return cmdline[index + 1]
    return None


def process_data_dir(env: Mapping[str, str], cwd: str) -> Path:
    """依程序的環境變數與工作目錄推得資料目錄（與 backend.config.get_paths 相同規則）。"""
    root = Path(cwd) / env.get("AP_DRAWART_ROOT", "")
    data = env.get("AP_DRAWART_DATA_DIR")
    return Path(cwd) / data if data else root / "data"


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def find_exhibition_processes(data_dir: Path, modules: Iterable[str] = CHILD_MODULES) -> list[Any]:
    """找出使用同一份資料目錄、以 `python -m <模組>` 執行的程序。

    不含自己與上層程序：venv 的 python.exe 是轉手程式，會以相同命令列再啟動實際的直譯器。
    """
    import psutil

    wanted = set(modules)
    own = {os.getpid()}
    try:
        own.update(parent.pid for parent in psutil.Process().parents())
    except psutil.Error:
        pass
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.pid in own:
            continue
        try:
            if launched_module(process.info.get("cmdline") or []) not in wanted:
                continue
            located = process_data_dir(process.environ(), process.cwd())
        except (psutil.Error, OSError):
            continue
        if _same_path(located, data_dir):
            found.append(process)
    return found


def stop_processes(processes: list[Any], timeout: float = 5.0) -> None:
    """先要求結束，逾時仍在執行的直接強制結束。"""
    import psutil

    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(processes, timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=timeout)


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
