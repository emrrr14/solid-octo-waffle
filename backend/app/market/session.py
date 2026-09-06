"""Trading-session state.

The frontend needs three distinct answers, and conflating them is the classic
source of "my portfolio says -100% at 3am" bugs:

* ``closed`` - show yesterday's close, no ticking, no P&L animation.
* ``pre``    - show yesterday's close, mark prices as indicative.
* ``open``   - stream, animate, compute day P&L against the official prev close.

Hours below are the regular sessions; for production wire ``exchange_calendars``
(``XNAS``, ``XIST``) so half-days and holidays are handled from the same source
of truth the exchange uses, rather than hand-maintained lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.domain.models import Venue


@dataclass(frozen=True, slots=True)
class SessionSpec:
    tz: str
    pre_open: time
    open: time
    close: time


SESSIONS: dict[Venue, SessionSpec] = {
    Venue.NASDAQ: SessionSpec("America/New_York", time(4, 0), time(9, 30), time(16, 0)),
    Venue.NYSE: SessionSpec("America/New_York", time(4, 0), time(9, 30), time(16, 0)),
    Venue.BIST: SessionSpec("Europe/Istanbul", time(9, 40), time(10, 0), time(18, 10)),
}


def session_state(venue: Venue, now: datetime | None = None) -> str:
    spec = SESSIONS.get(venue)
    if spec is None:                       # TEFAS / BES price once a day
        return "closed"
    local = (now or datetime.now(ZoneInfo(spec.tz))).astimezone(ZoneInfo(spec.tz))
    if local.weekday() >= 5:
        return "closed"
    if spec.open <= local.time() < spec.close:
        return "open"
    if spec.pre_open <= local.time() < spec.open:
        return "pre"
    return "closed"


def any_open(venues: list[Venue], now: datetime | None = None) -> str:
    """Aggregate state for a multi-venue portfolio: open wins, then pre."""
    states = {session_state(v, now) for v in venues}
    for state in ("open", "pre"):
        if state in states:
            return state
    return "closed"
