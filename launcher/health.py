"""與本機 Web 服務溝通：讀取 /api/health 與送出後臺要求。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

REQUEST_NAMES = ("shutdown", "relaunch_displays")


def pending_requests(health: dict[str, Any] | None) -> list[str]:
    """回傳待處理的 launcher_requests，結束展覽優先。"""
    requests = (health or {}).get("launcher_requests") or {}
    if not isinstance(requests, dict):
        return []
    return [name for name in REQUEST_NAMES if requests.get(name)]


def is_ready(health: dict[str, Any] | None) -> bool:
    """Web 已回應且資料庫可用即視為服務就緒（展示角色此時尚未連線）。"""
    if not health:
        return False
    database = (health.get("checks") or {}).get("database") or {}
    return bool(database.get("ok"))


def frontend_missing(health: dict[str, Any] | None) -> list[str]:
    frontend = ((health or {}).get("checks") or {}).get("frontend") or {}
    if frontend.get("ok", True):
        return []
    return list(frontend.get("missing") or ["frontend"])


class HealthClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        # 本機連線不經過系統代理
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(self, timeout: float = 2.0) -> dict[str, Any] | None:
        try:
            with self._opener.open(f"{self.base_url}/api/health", timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def post(self, path: str, payload: dict[str, Any] | None = None, timeout: float = 3.0) -> bool:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError):
            return False

    def clear_request(self, name: str) -> bool:
        return self.post("/api/admin/launcher-requests/clear", {"name": name})
