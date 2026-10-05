"""WebSocket router for live notifications, presence, and realtime telemetry."""

import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.security import decode_access_token
from app.modules.websocket.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSockets"])


def _extract_user_id(token: str | None) -> uuid.UUID | None:
    if not token:
        return None
    try:
        claims = decode_access_token(token)
        return claims.user_id
    except Exception:
        return None


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """General realtime stream. Accepts access token as query parameter: `?token=...`"""
    user_id = _extract_user_id(token)
    await manager.connect(websocket, user_id=user_id)
    try:
        # Send initial connected handshake
        await websocket.send_text(
            json.dumps({"type": "connected", "userId": str(user_id) if user_id else None})
        )
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                msg_type = parsed.get("type")
                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id=user_id)
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
        manager.disconnect(websocket, user_id=user_id)


@router.websocket("/ws/notifications")
async def websocket_notifications(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """Live notification stream for the connected user."""
    user_id = _extract_user_id(token)
    await manager.connect(websocket, user_id=user_id)
    try:
        await websocket.send_text(
            json.dumps({"type": "notifications_connected", "userId": str(user_id) if user_id else None})
        )
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id=user_id)
    except Exception as exc:
        logger.warning("WebSocket notification stream error: %s", exc)
        manager.disconnect(websocket, user_id=user_id)
