"""Worker 主迴圈：啟動恢復、逐件處理與心跳。"""

from __future__ import annotations

import threading

from backend import db
from backend.config import ai_provider_name, get_paths
from backend.logclient import get_logger
from backend.worker import jobs

log = get_logger("worker")


class WorkerRunner:
    def __init__(self, poll_interval: float = 1.0, heartbeat_interval: float = 5.0) -> None:
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.owner = jobs.worker_owner()
        self._current_job_id: int | None = None
        self._lock = threading.Lock()

    def _set_current(self, job_id: int | None) -> None:
        with self._lock:
            self._current_job_id = job_id
        self._beat()

    def _beat(self) -> None:
        with self._lock:
            job_id = self._current_job_id
        try:
            with db.connection() as conn:
                jobs.write_heartbeat(conn, job_id)
        except Exception:  # noqa: BLE001 - 心跳失敗不可中斷 Worker
            log.exception("worker.heartbeat_failed", "寫入 Worker 心跳失敗")

    def _heartbeat_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_interval):
            self._beat()

    def run(self, stop: threading.Event) -> None:
        get_paths().ensure_dirs()
        with db.connection() as conn:
            db.init_db(conn)
        log.info("worker.start", f"AI Worker 啟動（供應商：{ai_provider_name()}）")
        recovered = jobs.recover_interrupted_jobs()
        if recovered:
            log.info("worker.recovered", f"已處理 {recovered} 件中斷的工作")

        self._beat()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(stop,), name="worker-heartbeat", daemon=True
        )
        heartbeat.start()
        try:
            while not stop.is_set():
                try:
                    worked = jobs.process_next_job(owner=self.owner, on_claim=self._set_current)
                except Exception:  # noqa: BLE001 - 單件錯誤不可讓 Worker 停止
                    log.exception("worker.loop_error", "處理工作時發生錯誤，稍後重試")
                    stop.wait(2.0)
                    continue
                if not worked:
                    stop.wait(self.poll_interval)
        finally:
            stop.set()
            heartbeat.join(timeout=2.0)
            log.info("worker.stop", "AI Worker 結束")
