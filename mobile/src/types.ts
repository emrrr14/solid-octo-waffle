/**
 * Wire contract with the backend (`backend/app/api/ws.py`).
 *
 * These types are the client's half of a contract that must not drift: the
 * server sends one `snapshot` then `delta` frames carrying only what moved.
 * Every field the UI renders is computed server-side - the app never
 * multiplies a quantity by a price, because prices are licensed data and the
 * ledger is the server's.
 */

export type Session = 'open' | 'pre' | 'closed';

export interface PositionSnapshot {
  symbol: string;
  quantity: number;
  last_price: number;
  price_currency: string;
  fx_rate: number;
  market_value_base: number;
  prev_close_base: number;
  stale: boolean;
}

export interface PositionDelta {
  symbol: string;
  last_price: number;
  market_value: number;
  day_pnl: number;
  stale: boolean;
}

export interface SnapshotFrame {
  type: 'snapshot';
  ts: string;
  session: Session;
  base_currency: string;
  total_value: number;
  day_pnl: number;
  day_pnl_pct: number;
  positions: PositionSnapshot[];
}

export interface DeltaFrame {
  type: 'delta';
  ts: string;
  session: Session;
  total_value: number;
  day_pnl: number;
  day_pnl_pct: number;
  positions: PositionDelta[];
}

export interface HeartbeatFrame {
  type: 'heartbeat';
  ts: string;
  session: Session;
}

export type ServerFrame = SnapshotFrame | DeltaFrame | HeartbeatFrame;

/** One row of the portfolio list, after frames are folded together. */
export interface PositionView {
  symbol: string;
  quantity: number;
  lastPrice: number;
  currency: string;
  marketValue: number;
  dayPnl: number;
  dayPnlPct: number;
  stale: boolean;
  /** Direction of the most recent price change - drives the flash animation. */
  tick: 'up' | 'down' | 'flat';
}

export type ConnectionStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'offline';

/** REST payloads (backend/app/api/routes.py). */
export interface AllocationView {
  portfolio_id: string;
  /** Server-side id of this decision; doubles as the idempotency key. */
  decision_id: string;
  decided_at: string;
  trigger_reason: string;
  notional: number;
  base_currency: string;
  status: string;
  applied: boolean;
  note: string;
  expected_return_annual: number;
  risk_mad: number;
  turnover: number;
  binding_constraints: string[];
  rows: AllocationRow[];
}

export interface AllocationRow {
  symbol: string;
  name: string;
  asset_class: string;
  current_weight: number;
  target_weight: number;
  target_amount: number;
  order_amount: number;
}

export interface MacroEventView {
  id: string;
  series: string;
  kind: 'rate_hike' | 'rate_cut' | 'rate_hold' | 'inflation_print';
  observed_on: string;
  previous_value: number;
  new_value: number;
  change_bps: number;
  surprise_bps: number;
  triggered_rebalance: boolean;
  note: string;
}
