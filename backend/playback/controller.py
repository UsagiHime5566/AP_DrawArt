"""中央播放控制器。

Web 程序內唯一的播放調度者：以單一 asyncio 任務約每 200 毫秒推進一輪（engine.step），
管理三個展示角色的 WebSocket 連線，並廣播 prepare / commit / progress。
app 啟動時呼叫 start()，結束時呼叫 stop()。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar

from fastapi import WebSocket

from backend import db
from backend.config import DISPLAY_ROLES
from backend.logclient import get_logger
from backend.playback import engine, snapshots
from backend.timeutil import now_iso, now_utc

log = get_logger("playback")

T = TypeVar("T")

TICK_SECONDS = 0.2
PROGRESS_EVERY_TICKS = 5
SEND_TIMEOUT_SECONDS = 3.0
REPLACED_CLOSE_CODE = 4000


def _db_call(fn: Callable[..., T], *args: Any) -> T:
    with db.connection() as conn:
        return fn(conn, *args)


class DisplayConnection:
    def __init__(self, role: str, client_id: str, websocket: WebSocket) -> None:
        self.role = role
        self.client_id = client_id
        self.websocket = websocket
        self.closed = False

    async def send(self, payload: dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            await asyncio.wait_for(self.websocket.send_json(payload), SEND_TIMEOUT_SECONDS)
            return True
        except Exception:  # noqa: BLE001 - 對方已斷線或傳送逾時，交由重連處理
            await self.close(1011)
            return False

    async def close(self, code: int) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(Exception):
            await self.websocket.close(code=code)


class PlaybackController:
    def __init__(self) -> None:
        self.roles = {role: engine.RoleView() for role in DISPLAY_ROLES}
        self.connections: dict[str, DisplayConnection | None] = {role: None for role in DISPLAY_ROLES}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._clients_dirty = True
        self._last_progress: dict[str, Any] | None = None
        self._tick_count = 0

    async def start(self) -> None:
        await asyncio.to_thread(_db_call, engine.recover, now_utc())
        self._task = asyncio.create_task(self._run(), name="playback-controller")
        log.info("playback.start", "播放控制器啟動")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for role, connection in self.connections.items():
            if connection is not None:
                await connection.close(1001)
            self.connections[role] = None
            self.roles[role].connected = False
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_db_call, engine.save_clients, self.roles, now_utc())
        log.info("playback.stop", "播放控制器停止")

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 單輪失敗不停止播放
                log.exception("playback.error", "播放控制器推進失敗")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK_SECONDS)

    async def tick(self) -> None:
        async with self._lock:
            now = now_utc()
            roles = {role: replace(view) for role, view in self.roles.items()}
            persist_clients = self._clients_dirty
            self._clients_dirty = False
            want_progress = self._tick_count % PROGRESS_EVERY_TICKS == 0
            self._tick_count += 1

            def _work(conn: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
                if persist_clients:
                    engine.save_clients(conn, roles, now)
                messages = engine.step(conn, roles, now)
                progress = snapshots.progress(conn) if want_progress else None
                return messages, progress

            messages, progress = await asyncio.to_thread(_db_call, _work)
            for message in messages:
                await self._broadcast(message)
            if progress is not None and progress != self._last_progress:
                self._last_progress = progress
                await self._broadcast({"type": "progress", "progress": progress})

    async def _broadcast(self, message: dict[str, Any]) -> None:
        payload = {**message, "server_now": now_iso()}
        for connection in list(self.connections.values()):
            if connection is not None:
                await connection.send(payload)

    async def register(self, role: str, client_id: str, websocket: WebSocket) -> DisplayConnection:
        """登記新連線（取代同角色舊連線）並送出 hello。"""
        connection = DisplayConnection(role, client_id, websocket)
        remote = websocket.client.host if websocket.client else None
        async with self._lock:
            old = self.connections[role]
            self.connections[role] = connection
            self.roles[role] = engine.RoleView(
                connected=True,
                last_heartbeat=now_utc(),
                client_id=client_id,
                remote_addr=remote,
            )
            self._clients_dirty = True
            if old is not None:
                await old.close(REPLACED_CLOSE_CODE)
            hello = await asyncio.to_thread(_db_call, engine.hello_payload, role)
            await connection.send({**hello, "server_now": now_iso()})
        log.info(
            "display.connected",
            f"展示角色 {role} 已連線" + ("（取代舊連線）" if old is not None else ""),
            role=role,
            client_id=client_id,
            remote_addr=remote,
        )
        self.wake()
        return connection

    async def unregister(self, connection: DisplayConnection) -> None:
        connection.closed = True
        if self.connections.get(connection.role) is not connection:
            return
        self.connections[connection.role] = None
        self.roles[connection.role].connected = False
        self._clients_dirty = True
        log.warning(
            "display.disconnected",
            f"展示角色 {connection.role} 已斷線",
            role=connection.role,
            client_id=connection.client_id,
        )
        self.wake()

    def handle_message(self, connection: DisplayConnection, message: Any) -> None:
        if self.connections.get(connection.role) is not connection or not isinstance(message, dict):
            return
        view = self.roles[connection.role]
        now = now_utc()
        kind = message.get("type")
        revision = message.get("revision")
        valid_revision = isinstance(revision, int) and not isinstance(revision, bool)

        if kind == "ready" and valid_revision:
            view.ready_revision = revision
            view.last_heartbeat = now
        elif kind == "presented" and valid_revision:
            view.shown_revision = revision
            view.shown_at = now
            view.last_heartbeat = now
        elif kind == "heartbeat":
            view.last_heartbeat = now
            shown = message.get("shown_revision")
            if isinstance(shown, int) and not isinstance(shown, bool) and shown != view.shown_revision:
                view.shown_revision = shown
                view.shown_at = now
        elif kind == "client_error":
            text = str(message.get("message", ""))[:500]
            log.warning(
                "display.client_error",
                f"展示頁錯誤：{text}",
                role=connection.role,
                client_id=connection.client_id,
            )
            return
        else:
            return
        self._clients_dirty = True
        self.wake()


_controller: PlaybackController | None = None


def get_controller() -> PlaybackController | None:
    return _controller


async def start() -> None:
    global _controller
    _controller = PlaybackController()
    await _controller.start()


async def stop() -> None:
    global _controller
    if _controller is not None:
        await _controller.stop()
        _controller = None
