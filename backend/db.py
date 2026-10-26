"""SQLite 連線與結構初始化。

- 每個請求或工作循環各自開連線，用完即關；不跨執行緒共用連線。
- 連線為自動提交模式，需要原子性時使用 transaction()（BEGIN IMMEDIATE）。
- backend/schema/*.sql 依檔名排序執行，每個檔案都必須可重複執行（IF NOT EXISTS）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.config import get_paths

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def connect(db_file: Path | None = None) -> sqlite3.Connection:
    path = db_file or get_paths().db_file
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def connection(db_file: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_file)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection) -> None:
    """建立資料表並寫入預設值；已有資料時不覆寫。"""
    for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
        conn.executescript(sql_file.read_text(encoding="utf-8"))

    # 延遲匯入，避免循環相依
    from backend.services import settings, state

    with transaction(conn):
        settings.ensure_default_settings(conn)
        state.ensure_defaults(conn)
