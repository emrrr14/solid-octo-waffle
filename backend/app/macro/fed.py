"""FED policy-rate shift detection.

The authoritative, free source for the policy rate is FRED:

* ``DFEDTARU`` - upper limit of the federal funds target range (what the FOMC votes on)
* ``EFFR``     - effective federal funds rate (what actually trades; drifts intraday)

We watch ``DFEDTARU``: it is a step function, so "a shift happened" is simply
"today's value differs from the last value we stored".  ``EFFR`` is noisy and
must not be used as a trigger.

Two properties matter more than the HTTP call:

1. **Idempotency.**  FRED revises and re-publishes; the same decision must not
   fire two rebalances.  State is keyed on ``(series, value)`` plus the date the
   step was first seen.
2. **Surprise, not level.**  Markets price FOMC decisions in advance.  A 25bp cut
   that was fully priced moves nothing; a 25bp cut when futures implied a hold
   moves everything.  ``surprise_bps`` carries that, sourced from fed funds
   futures (CME FedWatch / a broker feed) and defaulting to the raw change when
   no expectation is available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

import httpx

from app.domain.models import MacroEvent, MacroEventKind, MacroSeries

log = logging.getLogger(__name__)

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True, slots=True)
class Observation:
    obs_date: date
    value: float


class RateStateStore(Protocol):
    """Persisted last-known state.  Backed by Postgres in production."""

    async def get_last(self, series: MacroSeries) -> Observation | None: ...
    async def set_last(self, series: MacroSeries, obs: Observation) -> None: ...


class InMemoryRateStateStore:
    def __init__(self) -> None:
        self._state: dict[MacroSeries, Observation] = {}

    async def get_last(self, series: MacroSeries) -> Observation | None:
        return self._state.get(series)

    async def set_last(self, series: MacroSeries, obs: Observation) -> None:
        self._state[series] = obs


class FredClient:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def latest_observations(self, series_id: str, limit: int = 5) -> list[Observation]:
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        resp = await self._client.get(FRED_URL, params=params)
        resp.raise_for_status()
        out: list[Observation] = []
        for row in resp.json().get("observations", []):
            if row["value"] in (".", "", None):  # FRED marks holidays with "."
                continue
            out.append(Observation(date.fromisoformat(row["date"]), float(row["value"])))
        return out  # newest first


def classify(change_bps: float, tol_bps: float = 0.5) -> MacroEventKind:
    if change_bps > tol_bps:
        return MacroEventKind.RATE_HIKE
    if change_bps < -tol_bps:
        return MacroEventKind.RATE_CUT
    return MacroEventKind.RATE_HOLD


class FedRateMonitor:
    """Polls FRED and emits a :class:`MacroEvent` only on a genuine step change.

    Poll it on a schedule (every 15 min is plenty - FRED publishes once a day,
    and the FOMC statement lands at 18:00 UTC on decision days).  Optionally
    couple it with a decision-day webhook for sub-minute reaction.
    """

    def __init__(
        self,
        fred: FredClient,
        store: RateStateStore,
        series: MacroSeries = MacroSeries.FED_TARGET_UPPER,
        min_change_bps: float = 5.0,
    ) -> None:
        self._fred = fred
        self._store = store
        self._series = series
        self._min_change_bps = min_change_bps

    async def poll(self, expected_rate_pct: float | None = None) -> MacroEvent | None:
        """Return an event if the policy rate stepped since the last poll.

        ``expected_rate_pct`` is the market-implied post-meeting rate (from fed
        funds futures) used to compute the surprise.  ``None`` -> surprise equals
        the realised change.
        """
        obs = await self._fred.latest_observations(self._series.value)
        if not obs:
            log.warning("fed: FRED returned no usable observations for %s", self._series)
            return None

        current = obs[0]
        last = await self._store.get_last(self._series)

        if last is None:
            # Cold start: adopt the current level silently, never fire on boot.
            await self._store.set_last(self._series, current)
            log.info("fed: cold start at %.2f%% (%s)", current.value, current.obs_date)
            return None

        change_bps = (current.value - last.value) * 100.0

        if abs(change_bps) < self._min_change_bps:
            # No step (or a rounding artefact).  Still advance the watermark so
            # a later revision of the same level cannot replay.
            if current.obs_date > last.obs_date:
                await self._store.set_last(self._series, current)
            return None

        if expected_rate_pct is None:
            surprise_bps = change_bps
        else:
            surprise_bps = (current.value - expected_rate_pct) * 100.0

        await self._store.set_last(self._series, current)

        event = MacroEvent(
            series=self._series,
            kind=classify(change_bps),
            observed_on=current.obs_date,
            previous_value=last.value,
            new_value=current.value,
            change_bps=change_bps,
            surprise_bps=surprise_bps,
            source="FRED",
        )
        log.info(
            "fed: %s %.0fbp (%.2f%% -> %.2f%%), surprise %.0fbp",
            event.kind.value, change_bps, last.value, current.value, surprise_bps,
        )
        return event


def detect_shift(
    previous: float,
    current: float,
    observed_on: date | None = None,
    expected_rate_pct: float | None = None,
    min_change_bps: float = 5.0,
    series: MacroSeries = MacroSeries.FED_TARGET_UPPER,
) -> MacroEvent | None:
    """Pure, dependency-free version of the detector - the unit-testable core."""
    change_bps = (current - previous) * 100.0
    if abs(change_bps) < min_change_bps:
        return None
    surprise_bps = change_bps if expected_rate_pct is None else (current - expected_rate_pct) * 100.0
    return MacroEvent(
        series=series,
        kind=classify(change_bps),
        observed_on=observed_on or datetime.utcnow().date(),
        previous_value=previous,
        new_value=current,
        change_bps=change_bps,
        surprise_bps=surprise_bps,
    )
