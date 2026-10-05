"""WebSocket connection manager for real-time notifications and live updates."""

import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> set of active WebSockets
        self.active_connections: dict[uuid.UUID, set[WebSocket]] = {}
        # broadcast listeners
        self.all_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, user_id: uuid.UUID | None = None) -> None:
        await websocket.accept()
        self.all_connections.add(websocket)
        if user_id:
            if user_id not in self.active_connections:
                self.active_connections[user_id] = set()
            self.active_connections[user_id].add(websocket)
        logger.info("WebSocket connected. User: %s (Total active: %d)", user_id, len(self.all_connections))

    def disconnect(self, websocket: WebSocket, user_id: uuid.UUID | None = None) -> None:
        self.all_connections.discard(websocket)
        if user_id and user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info("WebSocket disconnected. User: %s (Total active: %d)", user_id, len(self.all_connections))

    async def send_personal_message(self, user_id: uuid.UUID, message: dict[str, Any]) -> None:
        if user_id in self.active_connections:
            payload = json.dumps(message)
            dead = set()
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                self.disconnect(ws, user_id)

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        dead = set()
        for ws in self.all_connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
