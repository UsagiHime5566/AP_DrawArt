"""共用測試夾具：每個測試使用獨立的暫存資料、日誌與設定目錄。"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def paths_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把所有執行路徑導向暫存目錄，並放入一份 base.webp。"""
    base_image = tmp_path / "base.webp"
    shutil.copyfile(PROJECT_ROOT / "base.webp", base_image)
    monkeypatch.setenv("AP_DRAWART_ROOT", str(tmp_path))
    monkeypatch.setenv("AP_DRAWART_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AP_DRAWART_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AP_DRAWART_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("AP_DRAWART_BASE_IMAGE", str(base_image))
    monkeypatch.setenv("AP_DRAWART_STATIC_DIR", str(tmp_path / "static"))
    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "fake")
    monkeypatch.setenv("AP_DRAWART_ADMIN_ALLOW_ALL", "1")
    monkeypatch.delenv("AP_DRAWART_LOG_SINK", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from backend.config import get_paths

    paths = get_paths()
    paths.ensure_dirs()
    return paths


@pytest.fixture
def db_conn(paths_env) -> Iterator:
    from backend import db

    conn = db.connect()
    db.init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(paths_env) -> Iterator:
    """含 lifespan 的 TestClient（會啟動播放控制器）。"""
    from fastapi.testclient import TestClient

    from backend.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
