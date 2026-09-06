"""Auth flow through the real app: login, refresh rotation, replay, websocket."""
from __future__ import annotations

import pytest

from tests.conftest import EMAIL, PASSWORD, PORTFOLIO_ID


def test_login_returns_a_token_pair(client):
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"] and body["refresh_token"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": EMAIL, "password": "wrong-password-1"},
        {"email": "nobody@example.com", "password": PASSWORD},
    ],
)
def test_bad_credentials_are_indistinguishable(client, payload):
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_refresh_rotates_the_token(client, tokens):
    response = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert client.get(
        f"/api/portfolios/{PORTFOLIO_ID}/allocation",
        headers={"Authorization": f"Bearer {rotated['access_token']}"},
    ).status_code == 200


def test_replaying_a_used_refresh_token_kills_the_family(client, tokens):
    first = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()

    # The stolen (already-used) token is replayed...
    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    # ...which must also invalidate the legitimate device's current token.
    assert client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401


def test_logout_invalidates_the_device(client, tokens):
    assert client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_is_idempotent(client, tokens):
    client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204


def test_protected_routes_require_a_token(client):
    assert client.get(f"/api/portfolios/{PORTFOLIO_ID}/allocation").status_code == 401
    assert client.get("/api/macro/events").status_code == 401


def test_refresh_token_cannot_be_used_as_a_bearer_token(client, tokens):
    response = client.get(
        "/api/macro/events",
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )
    assert response.status_code == 401


def test_websocket_rejects_a_handshake_without_a_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws/portfolio/{PORTFOLIO_ID}"):
            pass
    assert excinfo.value.code == 4401


def test_websocket_rejects_a_forged_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            f"/ws/portfolio/{PORTFOLIO_ID}", subprotocols=["bearer", "forged.token.value"]
        ):
            pass
    assert excinfo.value.code == 4401


def test_websocket_rejects_someone_elses_portfolio(client, tokens):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/ws/portfolio/not-my-portfolio", subprotocols=["bearer", tokens["access_token"]]
        ):
            pass
    assert excinfo.value.code == 4404
