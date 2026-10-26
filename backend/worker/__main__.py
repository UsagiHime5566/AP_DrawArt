"""啟動 AI Worker：python -m backend.worker"""

from __future__ import annotations

import signal
import threading

from backend.worker.runner import WorkerRunner


def main() -> int:
    stop = threading.Event()

    def _handle(_signum: int, _frame: object) -> None:
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, _handle)

    WorkerRunner().run(stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
