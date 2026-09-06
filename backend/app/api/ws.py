"""Per-second portfolio valuation push.

Design, and the reasoning behind each choice:

* **Server-paced 1 Hz, not tick-paced.**  NVDA alone can print thousands of
  trades a second.  Forwarding each one would melt the browser's main thread
  and tell the user nothing a once-a-second number does not.  A ticker task
  coalesces: it always sends the *latest* state, never a backlog.
* **Snapshot then deltas.**  The first frame is the whole portfolio; after that
  only positions whose value moved, plus the totals.  A 30-position book goes
  from ~4 KB/s to a few hundred bytes/s per client.
* **Drop, never queue.**  If a client's socket is slow, skip its frame.  A
  buffered stream of stale valuations is worse than a gap - and an unbounded
  send queue is how one bad mobile connection takes down a pod.
* **Cadence follows the session.**  Markets closed -> one frame every 30s so the
  connection stays warm; markets open -> 1 Hz.  The moment the calendar flips to
  ``open`` the loop speeds up on its own, which is the "starts ticking when the
  market opens" behaviour, with no special-case code.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.models import Portfolio, PortfolioValuation
from app.market.cache import LastPriceCache
from app.market.valuation import FxConverter, value_portfolio
from app.security.tokens import TokenError, decode_access_token, extract_bearer_subprotocol

log = logging.getLogger(__name__)
router = APIRouter()

OPEN_INTERVAL = 1.0        # seconds between frames while a venue is open
IDLE_INTERVAL = 30.0       # while everything is closed
HEARTBEAT_INTERVAL = 15.0
EPSILON = 0.005            # money move below this is not worth a frame


def _snapshot_frame(val: PortfolioValuation) -> dict:
    return {
        "type": "snapshot",
        "ts": val.ts.isoformat(),
        "session": val.session,
        "base_currency": val.base_currency,
        "total_value": round(val.total_value, 2),
        "day_pnl": round(val.day_pnl, 2),
        "day_pnl_pct": round(val.day_pnl_pct, 4),
        "positions": [asdict(p) for p in val.positions],
    }


def _delta_frame(val: PortfolioValuation, previous: dict[str, float]) -> dict | None:
    changed = [
        {
            "symbol": p.symbol,
            "last_price": p.last_price,
            "market_value": round(p.market_value_base, 2),
            "day_pnl": round(p.day_pnl_base, 2),
            "stale": p.stale,
        }
        for p in val.positions
        if abs(p.market_value_base - previous.get(p.symbol, float("nan"))) > EPSILON
        or previous.get(p.symbol) is None
    ]
    if not changed:
        return None
    return {
        "type": "delta",
        "ts": val.ts.isoformat(),
        "session": val.session,
        "total_value": round(val.total_value, 2),
        "day_pnl": round(val.day_pnl, 2),
        "day_pnl_pct": round(val.day_pnl_pct, 4),
        "positions": changed,
    }


async def _send(ws: WebSocket, frame: dict, timeout: float = 2.0) -> bool:
    """Send with a deadline.  A timeout means the client is too slow: drop it."""
    try:
        await asyncio.wait_for(ws.send_text(json.dumps(frame, default=str)), timeout=timeout)
        return True
    except (asyncio.TimeoutError, RuntimeError):
        return False


async def stream_portfolio(
    ws: WebSocket,
    portfolio: Portfolio,
    cache: LastPriceCache,
    fx: FxConverter,
    token_expires_at: datetime | None = None,
) -> None:
    # Echo the "bearer" subprotocol the client offered; a client whose
    # subprotocol is not echoed closes the connection itself.
    await ws.accept(subprotocol="bearer")
    previous: dict[str, float] = {}
    last_heartbeat = 0.0
    loop = asyncio.get_running_loop()

    try:
        while True:
            started = loop.time()

            # A socket authorised once could otherwise outlive its access token
            # for hours.  Close on expiry and let the client reconnect with a
            # refreshed one - the reconnect path already exists and is tested.
            if token_expires_at is not None and datetime.now(timezone.utc) >= token_expires_at:
                log.info("ws: access token expired for %s, asking client to reauth",
                         portfolio.portfolio_id)
                await ws.close(code=4401)
                return

            valuation = await value_portfolio(portfolio, cache, fx)

            if not previous:
                frame = _snapshot_frame(valuation)
            else:
                frame = _delta_frame(valuation, previous)

            if frame is not None:
                if not await _send(ws, frame):
                    log.info("ws: dropping slow client for %s", portfolio.portfolio_id)
                    break
                previous = {p.symbol: p.market_value_base for p in valuation.positions}
                last_heartbeat = started
            elif started - last_heartbeat > HEARTBEAT_INTERVAL:
                # Nothing moved (closed market, halted symbol).  Keep the socket
                # and every proxy in between from reaping the connection.
                await _send(ws, {"type": "heartbeat",
                                 "ts": datetime.now(timezone.utc).isoformat(),
                                 "session": valuation.session})
                last_heartbeat = started

            interval = OPEN_INTERVAL if valuation.session in ("open", "pre") else IDLE_INTERVAL
            # Subtract the work we just did so the cadence stays on a 1s grid
            # instead of drifting to 1s + compute time.
            await asyncio.sleep(max(0.0, interval - (loop.time() - started)))

    except WebSocketDisconnect:
        log.info("ws: client disconnected from %s", portfolio.portfolio_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("ws: stream failed for %s", portfolio.portfolio_id)
        await ws.close(code=1011)


@router.websocket("/ws/portfolio/{portfolio_id}")
async def portfolio_socket(ws: WebSocket, portfolio_id: str) -> None:
    """Entry point.  Authentication happens *before* ``accept()``.

    The websocket handshake carries no Authorization header in any browser or
    React Native implementation, and a token in the query string ends up in
    proxy logs and crash reports - so the client sends
    ``Sec-WebSocket-Protocol: bearer, <access token>`` and we echo ``bearer``.

    Close codes are part of the client contract: 4401 means "reauthenticate and
    come back", 4404 means "stop retrying".
    """
    state = ws.app.state

    token = extract_bearer_subprotocol(ws.headers.get("sec-websocket-protocol"))
    if token is None:
        await ws.close(code=4401)
        return
    try:
        claims = decode_access_token(token, state.token_settings)
    except TokenError as exc:
        log.info("ws: rejected handshake for %s: %s", portfolio_id, exc)
        await ws.close(code=4401)
        return

    # Ownership is enforced in the repository, so an authenticated user asking
    # for someone else's portfolio gets the same answer as one asking for a
    # portfolio that does not exist.
    portfolio = await state.portfolios.get(portfolio_id, user_id=claims.user_id)
    if portfolio is None:
        await ws.close(code=4404)
        return

    await stream_portfolio(ws, portfolio, state.cache, state.fx, claims.expires_at)
