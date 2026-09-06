"""Core domain types shared by the market, macro, optimisation and API layers.

Money is always kept as ``Decimal`` at the boundaries (orders, cash balances)
and as ``float`` inside the numerical layers (regression, LP).  Anything that
crosses currencies carries its own currency tag; conversion happens once, in
:mod:`app.market.valuation`, never implicitly.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


class AssetClass(str, enum.Enum):
    EQUITY_TR = "equity_tr"          # BIST100 equities, TEFAS equity funds
    EQUITY_GLOBAL = "equity_global"  # NASDAQ / S&P 500 exposure
    BOND_TR = "bond_tr"              # government / corporate debt funds
    MONEY_MARKET = "money_market"    # likit / para piyasası funds
    GOLD = "gold"                    # gold and precious metal funds
    FX = "fx"                        # eurobond / FX-denominated funds


class Venue(str, enum.Enum):
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    BIST = "BIST"
    TEFAS = "TEFAS"   # end-of-day only
    BES = "BES"       # end-of-day only


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str                 # "NVDA", "THYAO.IS", "TEFAS:AFA"
    venue: Venue
    currency: str               # "USD", "TRY"
    asset_class: AssetClass
    streaming: bool             # True -> per-second ticks, False -> EOD NAV
    lot_size: Decimal = Decimal("1")


@dataclass(slots=True)
class Tick:
    """A normalised trade/quote print coming out of any upstream feed."""
    symbol: str
    price: float
    ts_event: datetime          # exchange timestamp (UTC)
    ts_ingest: datetime         # when our gateway saw it (UTC)
    size: float | None = None
    venue: Venue | None = None

    @property
    def latency_ms(self) -> float:
        return (self.ts_ingest - self.ts_event).total_seconds() * 1000.0


@dataclass(slots=True)
class Position:
    instrument: Instrument
    quantity: Decimal
    avg_cost: Decimal           # in instrument currency


@dataclass(slots=True)
class Portfolio:
    portfolio_id: str
    base_currency: str          # "TRY" for the 500 TL sleeve, "USD" for the equity sleeve
    positions: list[Position] = field(default_factory=list)
    cash: Decimal = Decimal("0")

    def symbols(self) -> list[str]:
        return [p.instrument.symbol for p in self.positions]


@dataclass(slots=True)
class PositionValuation:
    symbol: str
    quantity: float
    last_price: float
    price_currency: str
    fx_rate: float              # price_currency -> base_currency
    market_value_base: float
    prev_close_base: float
    stale: bool                 # last tick older than the staleness budget

    @property
    def day_pnl_base(self) -> float:
        return self.market_value_base - self.prev_close_base


@dataclass(slots=True)
class PortfolioValuation:
    portfolio_id: str
    base_currency: str
    ts: datetime
    total_value: float
    day_pnl: float
    day_pnl_pct: float
    positions: list[PositionValuation]
    session: str                # "pre", "open", "closed"


class MacroSeries(str, enum.Enum):
    FED_TARGET_UPPER = "DFEDTARU"   # FRED: federal funds target range, upper limit
    EFFR = "EFFR"                   # FRED: effective federal funds rate
    US_CPI = "CPIAUCSL"
    TR_POLICY_RATE = "TCMB_1W_REPO"
    TR_CPI = "TR_CPI_YOY"
    USDTRY = "USDTRY"


class MacroEventKind(str, enum.Enum):
    RATE_HIKE = "rate_hike"
    RATE_CUT = "rate_cut"
    RATE_HOLD = "rate_hold"
    INFLATION_PRINT = "inflation_print"


@dataclass(frozen=True, slots=True)
class MacroEvent:
    """An observed change in a macro series, already de-duplicated."""
    series: MacroSeries
    kind: MacroEventKind
    observed_on: date
    previous_value: float
    new_value: float
    change_bps: float           # signed, basis points (rates) or bps-equivalent
    surprise_bps: float = 0.0   # actual - consensus/market-implied expectation
    source: str = "FRED"

    @property
    def is_shift(self) -> bool:
        return abs(self.change_bps) >= 1e-9


@dataclass(frozen=True, slots=True)
class RebalanceTrigger:
    event: MacroEvent
    scenario: dict[str, float]  # macro factor name -> shock in the regression's units
    reason: str


@dataclass(slots=True)
class AllocationResult:
    weights: dict[str, float]        # symbol -> weight in [0, 1], sums to 1
    amounts: dict[str, Decimal]      # symbol -> money in base currency, sums to notional
    expected_return: float           # per-period, from the factor model
    risk_mad: float                  # mean absolute deviation of the portfolio
    turnover: float                  # sum |w_new - w_old| / 2
    binding_constraints: list[str]
    status: str                      # solver status
