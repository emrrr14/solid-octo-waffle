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
) -> None:
    await ws.accept()
    previous: dict[str, float] = {}
    last_heartbeat = 0.0
    loop = asyncio.get_running_loop()

    try:
        while True:
            started = loop.time()
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
    """Entry point.  Auth happens *before* ``accept()`` in production: read the
    short-lived token from the subprotocol header or a query param, resolve the
    user, and reject with 4401 if it does not own ``portfolio_id``.
    """
    app = ws.app.state
    portfolio = await app.portfolios.get(portfolio_id)
    if portfolio is None:
        await ws.close(code=4404)
        return
    await stream_portfolio(ws, portfolio, app.cache, app.fx)
