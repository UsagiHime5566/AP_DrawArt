"""AI Worker：領取順序、成功／失敗／逾時、遲到結果與重啟恢復。"""

from __future__ import annotations

import io
import json
import threading
import time
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.ai_providers import AIError, ImageRequest, ImageResult
from backend.ai_providers.fake import FakeImageProvider
from backend.config import FAILURE_TEXT
from backend.services import settings, state
from backend.timeutil import now_iso
from backend.worker import jobs
from backend.worker.runner import WorkerRunner


def _png_bytes(size: tuple[int, int] = (2048, 1152), color: str = "orange", fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


def _insert_settings(conn, **overrides) -> int:
    data = {**settings.DEFAULT_SETTINGS, **overrides}
    cursor = conn.execute(
        "INSERT INTO settings_versions (data_json, created_at, note) VALUES (?, ?, 'test')",
        (json.dumps(data, ensure_ascii=False), now_iso()),
    )
    return int(cursor.lastrowid)


def _artwork_id(conn) -> int:
    row = conn.execute("SELECT id FROM artworks LIMIT 1").fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        "INSERT INTO artworks (sha256, width, height, file_path, created_at) VALUES (?, 1600, 1132, ?, ?)",
        (uuid.uuid4().hex, "artworks/1/original.webp", now_iso()),
    )
    return int(cursor.lastrowid)


def make_job(conn, paths, receipt_order: int, settings_version_id: int | None = None, with_visitor: bool = True) -> dict:
    version_id = settings_version_id or settings.get_current(conn)[0]
    session_id = uuid.uuid4().hex
    submission_id = uuid.uuid4().hex
    now = now_iso()
    conn.execute(
        """
        INSERT INTO sessions (id, status, artwork_id, settings_version_id, started_at, deadline_at, ended_at)
        VALUES (?, 'submitted', ?, ?, ?, ?, ?)
        """,
        (session_id, _artwork_id(conn), version_id, now, now, now),
    )
    dir_path = f"submissions/{submission_id}"
    if with_visitor:
        directory = paths.data_dir / dir_path
        directory.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1920, 1080), "white")
        ImageDraw.Draw(image).line((200, 200, 1700, 900), fill="black", width=20)
        image.save(directory / "visitor.png")
    conn.execute(
        """
        INSERT INTO submissions (id, session_id, receipt_order, submission_key, content_sha256,
                                 dir_path, width, height, received_at)
        VALUES (?, ?, ?, ?, ?, ?, 1920, 1080, ?)
        """,
        (submission_id, session_id, receipt_order, uuid.uuid4().hex, "sha", dir_path, now),
    )
    cursor = conn.execute(
        """
        INSERT INTO ai_jobs (submission_id, receipt_order, settings_version_id, status, created_at, updated_at)
        VALUES (?, ?, ?, 'queued', ?, ?)
        """,
        (submission_id, receipt_order, version_id, now, now),
    )
    conn.execute(
        """
        INSERT INTO playback_items (submission_id, receipt_order, status, created_at, updated_at)
        VALUES (?, ?, 'waiting_for_ai', ?, ?)
        """,
        (submission_id, receipt_order, now, now),
    )
    return {"submission_id": submission_id, "job_id": int(cursor.lastrowid), "dir_path": dir_path}


def _job(conn, job_id: int):
    return conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (job_id,)).fetchone()


def _playback(conn, submission_id: str):
    return conn.execute("SELECT * FROM playback_items WHERE submission_id = ?", (submission_id,)).fetchone()


def _attempt(conn, attempt_id: str):
    return conn.execute("SELECT * FROM ai_attempts WHERE id = ?", (attempt_id,)).fetchone()


class StaticProvider:
    name = "static"

    def __init__(self, data: bytes, mime: str = "image/png") -> None:
        self.data = data
        self.mime = mime
        self.calls: list[ImageRequest] = []

    def generate(self, request: ImageRequest) -> ImageResult:
        self.calls.append(request)
        return ImageResult(self.data, self.mime, request_id="req_static", usage={"total_tokens": 1})


class FailingProvider:
    name = "failing"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ImageRequest) -> ImageResult:
        self.calls += 1
        raise AssertionError("不應呼叫供應商")


def test_fake_success_marks_playback_ready_generated(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)

    assert jobs.process_next_job(FakeImageProvider(delay_seconds=0, seed=1)) is True

    job = _job(db_conn, created["job_id"])
    assert job["status"] == "succeeded"
    assert job["finished_at"] and job["lease_owner"] is None
    attempt = _attempt(db_conn, job["current_attempt_id"])
    assert attempt["status"] == "succeeded"
    assert attempt["model"] == "gpt-image-2.5-sunburst"
    assert attempt["quality"] == "medium" and attempt["size"] == "2048x1152"
    assert attempt["result_file"] == f"{attempt['dir_path']}/ai-result.png"

    attempt_dir = paths_env.data_dir / attempt["dir_path"]
    assert (attempt_dir / "ai-input.png").is_file()
    with Image.open(attempt_dir / "ai-result.png") as result:
        assert result.size == (2048, 1152)
    metadata = json.loads((attempt_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "succeeded" and metadata["receipt_order"] == 1

    item = _playback(db_conn, created["submission_id"])
    assert item["status"] == "ready"
    assert item["result_kind"] == "generated"
    assert item["ai_attempt_id"] == attempt["id"]
    assert item["content_version"] == 1

    assert jobs.process_next_job(FakeImageProvider(delay_seconds=0)) is False
    log_text = paths_env.runtime_log.read_text(encoding="utf-8")
    assert "ai.start" in log_text and "ai.succeeded" in log_text


def test_provider_failure_produces_failure_card(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)

    jobs.process_next_job(FakeImageProvider(delay_seconds=0, fail_rate=1.0))

    job = _job(db_conn, created["job_id"])
    assert job["status"] == "failed" and job["error_code"] == "fake_failure"
    assert _attempt(db_conn, job["current_attempt_id"])["status"] == "failed"
    item = _playback(db_conn, created["submission_id"])
    assert item["status"] == "ready"
    assert item["result_kind"] == "failure_card"
    assert item["failure_text"] == FAILURE_TEXT
    assert item["ai_attempt_id"] is None
    attempt_dir = paths_env.data_dir / _attempt(db_conn, job["current_attempt_id"])["dir_path"]
    metadata = json.loads((attempt_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["failure_text"] == FAILURE_TEXT
    assert "ai.failed" in paths_env.runtime_log.read_text(encoding="utf-8")


def test_timeout_fails_and_late_result_is_discarded(db_conn, paths_env):
    version_id = _insert_settings(db_conn, ai_timeout_seconds=1)
    created = make_job(db_conn, paths_env, 1, settings_version_id=version_id)
    finished = threading.Event()

    class SlowProvider:
        name = "slow"

        def generate(self, request: ImageRequest) -> ImageResult:
            time.sleep(1.6)
            finished.set()
            return ImageResult(_png_bytes(), "image/png")

    started = time.monotonic()
    jobs.process_next_job(SlowProvider())
    assert time.monotonic() - started < 1.5

    job = _job(db_conn, created["job_id"])
    assert job["status"] == "failed" and job["error_code"] == "timeout"
    assert finished.wait(3)
    time.sleep(0.1)

    job = _job(db_conn, created["job_id"])
    assert job["status"] == "failed"
    attempt = _attempt(db_conn, job["current_attempt_id"])
    assert attempt["status"] == "failed" and attempt["result_file"] is None
    assert not (paths_env.data_dir / attempt["dir_path"] / "ai-result.png").exists()
    item = _playback(db_conn, created["submission_id"])
    assert item["result_kind"] == "failure_card" and item["content_version"] == 1


def test_jobs_are_processed_in_receipt_order(db_conn, paths_env):
    created = {order: make_job(db_conn, paths_env, order) for order in (3, 1, 2)}
    provider = StaticProvider(_png_bytes())

    while jobs.process_next_job(provider):
        pass

    by_dir = {item["dir_path"]: order for order, item in created.items()}
    called_orders = []
    for request in provider.calls:
        submission_dir = request.input_png.parent.parent.parent
        relative = submission_dir.relative_to(paths_env.data_dir).as_posix()
        called_orders.append(by_dir[relative])
    assert called_orders == [1, 2, 3]


def test_stale_attempt_cannot_overwrite_result(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)
    job = jobs.claim_next_job()
    assert job is not None
    assert jobs.finish_failure(job, "timeout", "逾時", None, 1000) is True

    saved = jobs.SavedResult(f"{job.attempt_dir}/ai-result.png", None, 2048, 1152)
    assert jobs.finish_success(job, saved, "req_late", None, 2000) is False
    assert jobs.finish_failure(job, "internal", "重複", None, 1) is False

    row = _job(db_conn, created["job_id"])
    assert row["status"] == "failed" and row["error_code"] == "timeout"
    item = _playback(db_conn, created["submission_id"])
    assert item["result_kind"] == "failure_card" and item["content_version"] == 1
    assert "ai.late_result" in paths_env.runtime_log.read_text(encoding="utf-8")


def test_recovery_archives_existing_result(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)
    job = jobs.claim_next_job()
    attempt_dir = paths_env.data_dir / job.attempt_dir
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "ai-result.png").write_bytes(_png_bytes())

    assert jobs.recover_interrupted_jobs() == 1

    row = _job(db_conn, created["job_id"])
    assert row["status"] == "succeeded"
    item = _playback(db_conn, created["submission_id"])
    assert item["status"] == "ready" and item["result_kind"] == "generated"
    assert item["ai_attempt_id"] == job.attempt_id
    assert "ai.recovered" in paths_env.runtime_log.read_text(encoding="utf-8")


def test_recovery_without_result_fails_without_calling_cloud(db_conn, paths_env, monkeypatch):
    created = make_job(db_conn, paths_env, 1)
    job = jobs.claim_next_job()
    attempt_dir = paths_env.data_dir / job.attempt_dir
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "ai-result.png").write_bytes(b"\x89PNG broken")
    provider = FailingProvider()
    monkeypatch.setattr(jobs, "get_provider", lambda: provider)

    jobs.recover_interrupted_jobs()

    row = _job(db_conn, created["job_id"])
    assert row["status"] == "failed" and row["error_code"] == "interrupted"
    item = _playback(db_conn, created["submission_id"])
    assert item["result_kind"] == "failure_card"
    assert provider.calls == 0
    assert jobs.process_next_job() is False


def test_missing_visitor_image_fails(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1, with_visitor=False)
    provider = FailingProvider()

    jobs.process_next_job(provider)

    row = _job(db_conn, created["job_id"])
    assert row["status"] == "failed" and row["error_code"] == "input_missing"
    assert provider.calls == 0


def test_undecodable_response_fails(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)

    jobs.process_next_job(StaticProvider(b"not an image"))

    assert _job(db_conn, created["job_id"])["error_code"] == "bad_response"
    assert _playback(db_conn, created["submission_id"])["result_kind"] == "failure_card"


def test_non_png_output_is_kept_and_converted(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)

    jobs.process_next_job(StaticProvider(_png_bytes(fmt="JPEG"), "image/jpeg"))

    job = _job(db_conn, created["job_id"])
    attempt = _attempt(db_conn, job["current_attempt_id"])
    attempt_dir = paths_env.data_dir / attempt["dir_path"]
    assert (attempt_dir / "ai-output.jpg").is_file()
    with Image.open(attempt_dir / "ai-result.png") as result:
        assert result.format == "PNG" and result.size == (2048, 1152)
    assert attempt["request_id"] == "req_static"
    assert json.loads(attempt["usage_json"]) == {"total_tokens": 1}


def test_unexpected_provider_exception_fails_job(db_conn, paths_env):
    created = make_job(db_conn, paths_env, 1)

    class Boom:
        name = "boom"

        def generate(self, request: ImageRequest) -> ImageResult:
            raise RuntimeError("boom")

    jobs.process_next_job(Boom())
    assert _job(db_conn, created["job_id"])["error_code"] == "internal"


def test_missing_openai_key_fails_job(db_conn, paths_env, monkeypatch):
    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "openai")
    created = make_job(db_conn, paths_env, 1)

    jobs.process_next_job()

    assert _job(db_conn, created["job_id"])["error_code"] == "not_configured"
    assert _playback(db_conn, created["submission_id"])["result_kind"] == "failure_card"


def test_heartbeat_updates_state_and_lease(db_conn, paths_env):
    make_job(db_conn, paths_env, 1)
    job = jobs.claim_next_job()
    db_conn.execute("UPDATE ai_jobs SET lease_heartbeat_at = 'old' WHERE id = ?", (job.job_id,))

    jobs.write_heartbeat(db_conn, job.job_id)

    beat = state.get(db_conn, "worker_heartbeat")
    assert beat["job_id"] == job.job_id and beat["pid"] > 0
    assert _job(db_conn, job.job_id)["lease_heartbeat_at"] != "old"


def test_call_with_deadline_raises_timeout():
    class Sleepy:
        name = "sleepy"

        def generate(self, request: ImageRequest) -> ImageResult:
            time.sleep(0.5)
            return ImageResult(b"", "image/png")

    request = ImageRequest(Path("x.png"), "p", "m", "medium", "2048x1152", 0.1)
    with pytest.raises(AIError) as error:
        jobs.call_with_deadline(Sleepy(), request)
    assert error.value.code == "timeout"


def test_runner_processes_jobs_and_stops(db_conn, paths_env, monkeypatch):
    monkeypatch.setenv("AP_DRAWART_FAKE_DELAY_SECONDS", "0")
    created = make_job(db_conn, paths_env, 1)
    stop = threading.Event()
    runner = WorkerRunner(poll_interval=0.05, heartbeat_interval=0.1)
    thread = threading.Thread(target=runner.run, args=(stop,), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _job(db_conn, created["job_id"])["status"] == "succeeded":
                break
            time.sleep(0.05)
        assert _job(db_conn, created["job_id"])["status"] == "succeeded"
        assert state.get(db_conn, "worker_heartbeat")["pid"] > 0
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    log_text = paths_env.runtime_log.read_text(encoding="utf-8")
    assert "worker.start" in log_text and "worker.stop" in log_text
