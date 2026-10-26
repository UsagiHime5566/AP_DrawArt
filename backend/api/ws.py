"""WS /ws/events?role=visitor|ai|artist&client_id=...：三個展示角色的同步通道。"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.config import DISPLAY_ROLES
from backend.logclient import get_logger
from backend.playback import controller

router = APIRouter()
log = get_logger("playback")

INVALID_ROLE_CLOSE_CODE = 4400
UNAVAILABLE_CLOSE_CODE = 1013


@router.websocket("/ws/events")
async def events(websocket: WebSocket, role: str = "", client_id: str = "") -> None:
    if role not in DISPLAY_ROLES:
        await websocket.close(code=INVALID_ROLE_CLOSE_CODE)
        return
    playback = controller.get_controller()
    if playback is None:
        await websocket.close(code=UNAVAILABLE_CLOSE_CODE)
        return

    await websocket.accept()
    connection = await playback.register(role, client_id[:64], websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                message = json.loads(text)
            except ValueError:
                continue
            playback.handle_message(connection, message)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 連線已被新連線取代並關閉
        pass
    finally:
        await playback.unregister(connection)
