"""The investment mandate: what the customer agreed the optimiser may do.

Stored as JSON on the portfolio row rather than as constants in code, because a
mandate is a per-customer agreement - a deploy must not be able to widen someone's
equity cap under them - and because the decision audit needs the mandate that was
in force at the time, not the one in today's build.

Everything is validated on the way in.  An LP fed a mandate whose class floors
sum above 1 is infeasible, and "infeasible" discovered at rebalance time on a
Wednesday evening is a much worse place to learn it than at save time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.optimization.simplex import AllocationConstraints, Objective

DEFAULT_TURNOVER_CAP = 0.6
DEFAULT_OBJECTIVE = Objective.MIN_RISK


class MandateError(ValueError):
    """The mandate cannot produce a solvable problem."""


@dataclass(slots=True)
class Mandate:
    universe: list[str]
    class_of: dict[str, str] = field(default_factory=dict)
    class_upper: dict[str, float] = field(default_factory=dict)
    class_lower: dict[str, float] = field(default_factory=dict)
    instrument_upper: dict[str, float] = field(default_factory=dict)
    turnover_cap: float = DEFAULT_TURNOVER_CAP
    min_return: float | None = None
    max_risk: float | None = None
    objective: Objective = DEFAULT_OBJECTIVE

    def validate(self) -> None:
        if not self.universe:
            raise MandateError("mandate has an empty universe")
        if len(set(self.universe)) != len(self.universe):
            raise MandateError("mandate universe contains duplicates")
        if not 0.0 <= self.turnover_cap <= 2.0:
            raise MandateError("turnover_cap must be between 0 and 2")

        floors = sum(self.class_lower.values())
        if floors > 1.0 + 1e-9:
            raise MandateError(f"class floors sum to {floors:.2f}, which cannot be invested")

        # Caps must leave room for a fully-invested book. Classes with no cap
        # are unbounded, so they can always absorb the remainder.
        capped = set(self.class_upper)
        present = {self.class_of.get(s, "unclassified") for s in self.universe}
        if capped >= present:
            headroom = sum(self.class_upper.get(c, 0.0) for c in present)
            if headroom < 1.0 - 1e-9:
                raise MandateError(f"class caps sum to {headroom:.2f}; the book cannot reach 100%")

        for cls, floor in self.class_lower.items():
            cap = self.class_upper.get(cls)
            if cap is not None and floor > cap + 1e-9:
                raise MandateError(f"class {cls}: floor {floor} exceeds cap {cap}")

        if self.objective is Objective.MAX_RETURN and self.max_risk is None:
            raise MandateError("max_return objective needs a max_risk budget")

    def to_constraints(self) -> AllocationConstraints:
        self.validate()
        return AllocationConstraints(
            upper=dict(self.instrument_upper),
            class_of=dict(self.class_of),
            class_lower=dict(self.class_lower),
            class_upper=dict(self.class_upper),
            turnover_cap=self.turnover_cap,
            min_return=self.min_return,
            max_risk=self.max_risk,
        )


def parse_mandate(raw: dict, class_of: dict[str, str] | None = None) -> Mandate:
    """Build a mandate from the portfolio's JSON column.

    ``class_of`` comes from the instruments table, not the JSON: asset class is a
    property of the instrument, and duplicating it into every customer's mandate
    is how two customers end up with the same fund in different classes.
    """
    objective_raw = str(raw.get("objective", DEFAULT_OBJECTIVE.value))
    try:
        objective = Objective(objective_raw)
    except ValueError as exc:
        raise MandateError(f"unknown objective {objective_raw!r}") from exc

    mandate = Mandate(
        universe=list(raw.get("universe", [])),
        class_of=dict(class_of or {}),
        class_upper={k: float(v) for k, v in (raw.get("class_upper") or {}).items()},
        class_lower={k: float(v) for k, v in (raw.get("class_lower") or {}).items()},
        instrument_upper={k: float(v) for k, v in (raw.get("instrument_upper") or {}).items()},
        turnover_cap=float(raw.get("turnover_cap", DEFAULT_TURNOVER_CAP)),
        min_return=None if raw.get("min_return") is None else float(raw["min_return"]),
        max_risk=None if raw.get("max_risk") is None else float(raw["max_risk"]),
        objective=objective,
    )
    mandate.validate()
    return mandate
