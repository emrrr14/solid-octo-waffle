"""The endpoints the iOS client actually calls, end to end."""
from __future__ import annotations

import json

from tests.conftest import OTHER_EMAIL, OTHER_PASSWORD, PORTFOLIO_ID


def test_allocation_maps_the_stored_decision(client, auth_headers, app):
    response = client.get(f"/api/portfolios/{PORTFOLIO_ID}/allocation", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["decision_id"] == app.state.seeded["decision_id"]
    assert body["notional"] == 500.0
    assert body["base_currency"] == "TRY"
    assert body["binding_constraints"] == ["turnover_budget"]
    # Daily -> annual happens once, on the server.
    assert body["expected_return_annual"] == 0.001 * 252

    rows = {row["symbol"]: row for row in body["rows"]}
    assert rows["NVDA"]["current_weight"] == 0.6
    assert rows["NVDA"]["target_weight"] == 0.4
    assert rows["NVDA"]["order_amount"] == -100.0
    assert rows["GLD"]["name"] == "Altın Fonu"       # joined from instruments
    assert rows["GLD"]["asset_class"] == "gold"


def test_another_users_portfolio_is_a_404(client):
    """An id you do not own must look exactly like an id that does not exist."""
    other = client.post(
        "/api/auth/login", json={"email": OTHER_EMAIL, "password": OTHER_PASSWORD}
    ).json()
    response = client.get(
        f"/api/portfolios/{PORTFOLIO_ID}/allocation",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert response.status_code == 404


def test_macro_events_are_newest_first(client, auth_headers):
    response = client.get("/api/macro/events", headers=auth_headers)
    assert response.status_code == 200
    events = response.json()
    assert events[0]["series"] == "DFEDTARU"
    assert events[0]["surprise_bps"] == -25.0
    assert events[0]["triggered_rebalance"] is True


def test_confirm_requires_an_idempotency_key(client, auth_headers):
    response = client.post(f"/api/portfolios/{PORTFOLIO_ID}/rebalance/confirm", headers=auth_headers)
    assert response.status_code == 400


def test_confirm_is_idempotent(client, auth_headers, app):
    decision_id = app.state.seeded["decision_id"]
    headers = {**auth_headers, "Idempotency-Key": decision_id}

    first = client.post(f"/api/portfolios/{PORTFOLIO_ID}/rebalance/confirm", headers=headers)
    second = client.post(f"/api/portfolios/{PORTFOLIO_ID}/rebalance/confirm", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"accepted": True, "decision_id": decision_id}


def test_confirming_an_unknown_decision_is_a_404(client, auth_headers):
    response = client.post(
        f"/api/portfolios/{PORTFOLIO_ID}/rebalance/confirm",
        headers={**auth_headers, "Idempotency-Key": "does-not-exist"},
    )
    assert response.status_code == 404


def test_websocket_streams_a_snapshot_to_an_authorised_client(client, tokens):
    with client.websocket_connect(
        f"/ws/portfolio/{PORTFOLIO_ID}", subprotocols=["bearer", tokens["access_token"]]
    ) as ws:
        frame = json.loads(ws.receive_text())

    assert frame["type"] == "snapshot"
    assert frame["base_currency"] == "TRY"
    # 2 NVDA @ 110 USD * 34 + 100 GLD @ 1.10 TRY + 100 cash
    assert frame["total_value"] == 2 * 110 * 34 + 100 * 1.10 + 100
    assert {p["symbol"] for p in frame["positions"]} == {"NVDA", "GLD"}
