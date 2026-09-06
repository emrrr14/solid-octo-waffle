"""Turning a macro event into a rebalance instruction.

Two jobs live here and nowhere else:

* **Scenario mapping** - a :class:`MacroEvent` is a fact about the world
  ("target range cut 25bp, 25bp more than priced"); the factor model speaks in
  regressor units.  ``build_scenario`` is the single translation point, so the
  regression and the LP can never disagree about what a shock means.
* **Governance** - cooldowns, a minimum surprise threshold and a
  minimum-drift rule.  Without them a revised FRED print or a 5bp EFFR wobble
  churns every customer's 500 TL through fund entry/exit fees and eats the
  return the model just predicted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.domain.models import MacroEvent, MacroEventKind, MacroSeries, RebalanceTrigger

log = logging.getLogger(__name__)

# Factor names must match the columns of the regression panel exactly.
F_FED_CHANGE = "d_fed_bps"
F_FED_SURPRISE = "fed_surprise_bps"
F_TR_CPI_SURPRISE = "tr_cpi_surprise_pp"
F_USDTRY = "d_log_usdtry"


def build_scenario(event: MacroEvent) -> dict[str, float]:
    """Map an event onto the regression's factor space.

    The surprise, not the level change, is what carries information; both are
    passed because funds with different duration load on them differently.
    USDTRY is included with a sign convention that a *hawkish* US surprise
    (positive bp) implies TRY depreciation - it is a first-order pass-through
    estimate, refreshed from the same panel, not a hard-coded belief.
    """
    if event.series in (MacroSeries.FED_TARGET_UPPER, MacroSeries.EFFR):
        return {
            F_FED_CHANGE: event.change_bps,
            F_FED_SURPRISE: event.surprise_bps,
            F_USDTRY: 0.0004 * event.surprise_bps,
        }
    if event.kind is MacroEventKind.INFLATION_PRINT:
        return {F_TR_CPI_SURPRISE: event.surprise_bps / 100.0}
    return {}


@dataclass(slots=True)
class TriggerPolicy:
    min_surprise_bps: float = 10.0     # ignore fully-priced moves
    cooldown: timedelta = timedelta(hours=6)
    min_weight_drift: float = 0.02     # don't trade for less than 2% of the sleeve


class TriggerRouter:
    def __init__(self, policy: TriggerPolicy | None = None) -> None:
        self._policy = policy or TriggerPolicy()
        self._last_fired: dict[MacroSeries, datetime] = {}

    def evaluate(self, event: MacroEvent, now: datetime | None = None) -> RebalanceTrigger | None:
        now = now or datetime.now(timezone.utc)

        if not event.is_shift:
            return None
        if abs(event.surprise_bps) < self._policy.min_surprise_bps:
            log.info("trigger suppressed: %.0fbp surprise below threshold", event.surprise_bps)
            return None

        last = self._last_fired.get(event.series)
        if last is not None and now - last < self._policy.cooldown:
            log.info("trigger suppressed: %s still in cooldown", event.series.value)
            return None

        scenario = build_scenario(event)
        if not scenario:
            return None

        self._last_fired[event.series] = now
        return RebalanceTrigger(
            event=event,
            scenario=scenario,
            reason=(
                f"{event.kind.value} {event.change_bps:+.0f}bp on {event.observed_on} "
                f"({event.surprise_bps:+.0f}bp vs expectation)"
            ),
        )

    def is_material(self, current: dict[str, float], target: dict[str, float]) -> bool:
        """Drift gate: would the trade actually be worth its cost?"""
        drift = sum(abs(target.get(s, 0.0) - current.get(s, 0.0)) for s in set(current) | set(target))
        return drift / 2.0 >= self._policy.min_weight_drift
