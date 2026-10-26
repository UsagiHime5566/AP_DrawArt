"""集中日誌寫入器：接收各程序送來的事件，單獨寫入 logs/runtime.txt。"""

from backend.logsink.server import DEFAULT_PORT, LogSinkServer, make_record, normalize_record

__all__ = ["DEFAULT_PORT", "LogSinkServer", "make_record", "normalize_record"]
