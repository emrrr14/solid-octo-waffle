from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.models import MacroEventKind
from app.macro.fed import classify, detect_shift
from app.macro.triggers import TriggerPolicy, TriggerRouter


def test_cut_is_detected_with_surprise():
    event = detect_shift(5.50, 5.25, date(2026, 9, 5), expected_rate_pct=5.50)
    assert event.kind is MacroEventKind.RATE_CUT
    assert event.change_bps == pytest.approx(-25.0)
    assert event.surprise_bps == pytest.approx(-25.0)


def test_fully_priced_cut_has_no_surprise():
    event = detect_shift(5.50, 5.25, expected_rate_pct=5.25)
    assert event.surprise_bps == pytest.approx(0.0)


def test_noise_below_threshold_is_not_a_shift():
    assert detect_shift(5.50, 5.502) is None


def test_classify_boundaries():
    assert classify(25.0) is MacroEventKind.RATE_HIKE
    assert classify(-25.0) is MacroEventKind.RATE_CUT
    assert classify(0.0) is MacroEventKind.RATE_HOLD


def test_priced_in_move_does_not_trigger_a_rebalance():
    router = TriggerRouter()
    event = detect_shift(5.50, 5.25, expected_rate_pct=5.25)
    assert router.evaluate(event) is None


def test_cooldown_suppresses_the_second_trigger():
    router = TriggerRouter(TriggerPolicy(cooldown=timedelta(hours=6)))
    now = datetime(2026, 9, 5, 18, 0, tzinfo=timezone.utc)
    first = detect_shift(5.50, 5.25, expected_rate_pct=5.50)
    assert router.evaluate(first, now) is not None
    second = detect_shift(5.25, 5.00, expected_rate_pct=5.25 + 0.25)
    assert router.evaluate(second, now + timedelta(hours=1)) is None


def test_scenario_carries_change_and_surprise():
    router = TriggerRouter()
    trigger = router.evaluate(detect_shift(5.50, 5.25, expected_rate_pct=5.50))
    assert trigger.scenario["d_fed_bps"] == pytest.approx(-25.0)
    assert trigger.scenario["fed_surprise_bps"] == pytest.approx(-25.0)


def test_drift_gate():
    router = TriggerRouter(TriggerPolicy(min_weight_drift=0.05))
    current = {"A": 0.50, "B": 0.50}
    assert not router.is_material(current, {"A": 0.52, "B": 0.48})
    assert router.is_material(current, {"A": 0.70, "B": 0.30})
