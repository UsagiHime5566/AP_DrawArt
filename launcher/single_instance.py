"""單一執行個體鎖：Windows 使用命名 mutex，其他平台使用檔案鎖。"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

_ERROR_ALREADY_EXISTS = 183


def instance_name(data_dir: Path) -> str:
    """以資料目錄區分不同交付包，同一份資料只允許一個啟動器。"""
    digest = hashlib.sha1(str(data_dir.resolve()).lower().encode("utf-8")).hexdigest()[:12]
    return f"Local\\AP_DrawArt_Launcher_{digest}"


class SingleInstance:
    def __init__(self, name: str, lock_dir: Path | None = None) -> None:
        self.name = name
        self._lock_dir = lock_dir
        self._handle: int | None = None
        self._lock_file = None

    def acquire(self) -> bool:
        """取得鎖；已有其他執行個體時回傳 False。"""
        if sys.platform == "win32":
            return self._acquire_mutex()
        return self._acquire_file_lock()

    def release(self) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
            self._handle = None
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def _acquire_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, "CreateMutexW 失敗")
        if error == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def _acquire_file_lock(self) -> bool:
        import fcntl

        lock_dir = self._lock_dir or Path(os.environ.get("TMPDIR", "/tmp"))
        safe_name = self.name.replace("\\", "_")
        handle = open(lock_dir / f"{safe_name}.lock", "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._lock_file = handle
        return True
