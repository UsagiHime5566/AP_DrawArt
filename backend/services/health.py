"""健康檢查：啟動器、後臺與展示頁判斷主機是否可開展。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from backend import exhibition, logclient
from backend.config import ai_provider_name, config_file_error, get_paths, http_port, read_openai_key
from backend.playback import status as playback_status
from backend.services import disk, network, state
from backend.timeutil import now_utc, parse_iso

WORKER_HEARTBEAT_TIMEOUT_SECONDS = 15
FRONTEND_PAGES = ("exhibition", "admin")


def _check_database(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        conn.execute("SELECT 1").fetchone()
        return {"ok": True}
    except sqlite3.Error as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def _check_frontend() -> dict[str, Any]:
    static_dir = get_paths().static_dir
    missing = [page for page in FRONTEND_PAGES if not (static_dir / page / "index.html").is_file()]
    return {"ok": not missing, "missing": missing}


def _check_artwork(conn: sqlite3.Connection) -> dict[str, Any]:
    artwork_id = state.get(conn, "active_artwork_id")
    exists = (
        artwork_id is not None
        and conn.execute("SELECT 1 FROM artworks WHERE id = ?", (artwork_id,)).fetchone() is not None
    )
    return {"ok": exists, "artwork_id": artwork_id}


def _check_worker(conn: sqlite3.Connection) -> dict[str, Any]:
    heartbeat = state.get(conn, "worker_heartbeat")
    if not heartbeat or not heartbeat.get("at"):
        return {"ok": False, "pid": None, "last_heartbeat_at": None}
    age = (now_utc() - parse_iso(heartbeat["at"])).total_seconds()
    return {
        "ok": age <= WORKER_HEARTBEAT_TIMEOUT_SECONDS,
        "pid": heartbeat.get("pid"),
        "last_heartbeat_at": heartbeat["at"],
    }


def _check_displays(conn: sqlite3.Connection) -> dict[str, Any]:
    view = exhibition.payload(conn)
    roles = {"exhibition": {
        "connected": view["connected"], "shown_revision": view["revision"],
        "last_heartbeat_at": exhibition.load(conn)["last_seen"],
    }}
    return {"ok": view["connected"], "roles": roles}


def _check_openai_key() -> dict[str, Any]:
    _, source = read_openai_key()
    if ai_provider_name() == "fake":
        return {"ok": True, "source": source or "fake"}
    result: dict[str, Any] = {"ok": source is not None, "source": source}
    if source is None:
        result["error"] = config_file_error(get_paths().openai_key_file)
    return result


def _run_check(check: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """單項檢查出錯只把該項標為失敗；健康檢查本身必須照常回應，啟動器靠它判斷 Web 是否就緒。"""
    try:
        return check()
    except Exception as error:  # noqa: BLE001 - 任何單項問題都不能讓整個健康檢查失敗
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def admin_urls() -> list[str]:
    port = http_port()
    return [f"http://{ip}:{port}/admin" for ip in network.lan_ipv4_addresses()]


def get_health(conn: sqlite3.Connection) -> dict[str, Any]:
    log_status = logclient.status()
    checks = {
        "database": _check_database(conn),
        "frontend": _run_check(_check_frontend),
        "artwork": _run_check(lambda: _check_artwork(conn)),
        "worker": _run_check(lambda: _check_worker(conn)),
        "displays": _run_check(lambda: _check_displays(conn)),
        "disk": _run_check(disk.disk_status),
        "log": {
            "ok": log_status["ok"],
            "mode": log_status["mode"],
            "last_error": log_status["last_error"],
        },
        "openai_key": _run_check(_check_openai_key),
    }
    if not checks["openai_key"]["ok"]:
        overall = "setup_required"
    elif all(check["ok"] for check in checks.values()):
        overall = "ok"
    else:
        overall = "degraded"
    return {
        "status": overall,
        "admin_urls": admin_urls(),
        "launcher_requests": state.get(
            conn, "launcher_requests", {"shutdown": False, "relaunch_displays": False}
        ),
        "checks": checks,
    }
