"""Mandate validation - the guard that keeps an infeasible LP out of production."""
from __future__ import annotations

import pytest

from app.optimization.simplex import Objective
from app.rebalance.mandate import MandateError, parse_mandate

CLASSES = {"AAA": "equity", "BBB": "bond", "CCC": "money"}
BASE = {"universe": ["AAA", "BBB", "CCC"]}


def test_defaults_are_sensible():
    mandate = parse_mandate(BASE, CLASSES)
    assert mandate.objective is Objective.MIN_RISK
    assert mandate.turnover_cap == 0.6
    assert mandate.min_return is None


def test_constraints_carry_the_instrument_classes_from_the_database():
    mandate = parse_mandate({**BASE, "class_upper": {"equity": 0.4}}, CLASSES)
    constraints = mandate.to_constraints()
    assert constraints.class_of == CLASSES
    assert constraints.class_upper == {"equity": 0.4}


@pytest.mark.parametrize(
    "raw,message",
    [
        ({"universe": []}, "empty universe"),
        ({"universe": ["AAA", "AAA"]}, "duplicates"),
        ({**BASE, "turnover_cap": 3.0}, "turnover_cap"),
        ({**BASE, "class_lower": {"equity": 0.7, "bond": 0.5}}, "cannot be invested"),
        ({**BASE, "class_lower": {"equity": 0.5}, "class_upper": {"equity": 0.3}}, "exceeds cap"),
        ({**BASE, "objective": "maximise_everything"}, "unknown objective"),
        ({**BASE, "objective": "max_return"}, "max_risk budget"),
    ],
)
def test_unsolvable_mandates_are_rejected_at_parse_time(raw, message):
    with pytest.raises(MandateError, match=message):
        parse_mandate(raw, CLASSES)


def test_caps_that_cannot_reach_a_full_book_are_rejected():
    # Every class capped, and the caps sum to 0.6: no allocation can be
    # fully invested, so the LP would be infeasible at rebalance time.
    raw = {**BASE, "class_upper": {"equity": 0.3, "bond": 0.2, "money": 0.1}}
    with pytest.raises(MandateError, match="cannot reach 100%"):
        parse_mandate(raw, CLASSES)


def test_an_uncapped_class_can_absorb_the_remainder():
    raw = {**BASE, "class_upper": {"equity": 0.3, "bond": 0.2}}  # money uncapped
    assert parse_mandate(raw, CLASSES).to_constraints().class_upper["equity"] == 0.3
