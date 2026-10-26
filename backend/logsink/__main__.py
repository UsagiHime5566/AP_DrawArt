"""獨立執行集中日誌寫入器：python -m backend.logsink [--host 127.0.0.1] [--port 8765]"""

from __future__ import annotations

import argparse
import time

from backend.config import log_sink_address
from backend.logsink.server import DEFAULT_PORT, LogSinkServer, make_record


def main(argv: list[str] | None = None) -> int:
    configured = log_sink_address()
    parser = argparse.ArgumentParser(prog="python -m backend.logsink", description="集中日誌寫入器")
    parser.add_argument("--host", default=configured[0] if configured else "127.0.0.1")
    parser.add_argument("--port", type=int, default=configured[1] if configured else DEFAULT_PORT)
    args = parser.parse_args(argv)

    sink = LogSinkServer(args.host, args.port)
    sink.start()
    sink.submit(make_record("INFO", "logsink.start", f"集中日誌寫入器啟動於 {sink.address}"))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sink.submit(make_record("INFO", "logsink.stop", "集中日誌寫入器結束"))
        sink.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
