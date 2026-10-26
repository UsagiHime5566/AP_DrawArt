"""後臺來源 IP 限制：/admin 與 /api/admin/* 只接受本機迴路或與主機同網段的來源。"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.api.errors import error_body
from backend.logclient import get_logger
from backend.services.network import is_admin_client_allowed

log = get_logger("web")


def is_admin_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in ("/admin", "/api/admin"))


class AdminNetGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not is_admin_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        host = client[0] if client else None
        if is_admin_client_allowed(host):
            await self.app(scope, receive, send)
            return

        log.warning("admin.forbidden", "拒絕非同網段的後臺存取", remote_addr=host, path=scope.get("path"))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            error_body("forbidden_network", "後臺只接受與主機同網段的裝置"), status_code=403
        )
        await response(scope, receive, send)
