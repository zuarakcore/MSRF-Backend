"""Integration tests for WebSockets realtime stream and notifications."""

import json

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from tests.conftest import UserFactory


def test_websocket_connect_and_ping_pong() -> None:
    client = TestClient(app, base_url="http://test")
    with client.websocket_connect("/api/v1/ws", headers={"host": "test"}) as websocket:
        data = json.loads(websocket.receive_text())
        assert data["type"] == "connected"
        websocket.send_text(json.dumps({"type": "ping"}))
        pong = json.loads(websocket.receive_text())
        assert pong["type"] == "pong"


async def test_websocket_notifications_stream_with_token(make_user: UserFactory) -> None:
    user = await make_user(email="ws_user@example.com")
    token, _ = create_access_token(user_id=user.id, role=user.role.value, token_version=user.token_version)

    client = TestClient(app, base_url="http://test")
    with client.websocket_connect(
        f"/api/v1/ws/notifications?token={token}", headers={"host": "test"}
    ) as websocket:
        data = json.loads(websocket.receive_text())
        assert data["type"] == "notifications_connected"
        assert data["userId"] == str(user.id)
        websocket.send_text(json.dumps({"type": "ping"}))
        pong = json.loads(websocket.receive_text())
        assert pong["type"] == "pong"
