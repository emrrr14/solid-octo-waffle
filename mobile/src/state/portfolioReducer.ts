/**
 * Pure fold of server frames into render state.
 *
 * Kept free of React and zustand so the merge rules - which are where live
 * financial UIs actually go wrong - can be tested directly.  Three rules:
 *
 * 1. A `delta` before any `snapshot` is dropped.  Deltas carry only what moved;
 *    applying one to an empty book invents a portfolio that does not exist.
 * 2. A symbol in a delta that we have never seen is dropped for the same reason
 *    (and triggers a resync flag) rather than being rendered with quantity 0.
 * 3. Tick direction is derived here, once, from the previous price - not in the
 *    component, which would recompute it on every unrelated re-render.
 */

import type { DeltaFrame, PositionView, Session, SnapshotFrame } from '../types';

export interface PortfolioState {
  hasSnapshot: boolean;
  baseCurrency: string;
  session: Session;
  totalValue: number;
  dayPnl: number;
  dayPnlPct: number;
  updatedAt: string | null;
  /** Display order, fixed by the snapshot so rows never jump around. */
  order: string[];
  positions: Record<string, PositionView>;
  /** Set when a delta referenced an unknown symbol: the client must resync. */
  needsResync: boolean;
}

export const initialPortfolioState: PortfolioState = {
  hasSnapshot: false,
  baseCurrency: 'TRY',
  session: 'closed',
  totalValue: 0,
  dayPnl: 0,
  dayPnlPct: 0,
  updatedAt: null,
  order: [],
  positions: {},
  needsResync: false,
};

function pnlPct(marketValue: number, dayPnl: number): number {
  const base = marketValue - dayPnl;
  return base === 0 ? 0 : (dayPnl / base) * 100;
}

function direction(previous: number | undefined, next: number): PositionView['tick'] {
  if (previous === undefined || previous === next) return 'flat';
  return next > previous ? 'up' : 'down';
}

export function applySnapshot(state: PortfolioState, frame: SnapshotFrame): PortfolioState {
  const positions: Record<string, PositionView> = {};
  for (const p of frame.positions) {
    const dayPnl = p.market_value_base - p.prev_close_base;
    positions[p.symbol] = {
      symbol: p.symbol,
      quantity: p.quantity,
      lastPrice: p.last_price,
      currency: p.price_currency,
      marketValue: p.market_value_base,
      dayPnl,
      dayPnlPct: pnlPct(p.market_value_base, dayPnl),
      stale: p.stale,
      // A snapshot is a fresh start (first connect or resync); flashing every
      // row green on reconnect would be noise, not information.
      tick: 'flat',
    };
  }
  return {
    hasSnapshot: true,
    baseCurrency: frame.base_currency,
    session: frame.session,
    totalValue: frame.total_value,
    dayPnl: frame.day_pnl,
    dayPnlPct: frame.day_pnl_pct,
    updatedAt: frame.ts,
    order: frame.positions.map((p) => p.symbol),
    positions,
    needsResync: false,
  };
}

export function applyDelta(state: PortfolioState, frame: DeltaFrame): PortfolioState {
  if (!state.hasSnapshot) return state;

  let needsResync = state.needsResync;
  const positions = { ...state.positions };

  for (const d of frame.positions) {
    const previous = positions[d.symbol];
    if (!previous) {
      needsResync = true; // server knows about a position we don't - ask for a snapshot
      continue;
    }
    positions[d.symbol] = {
      ...previous,
      lastPrice: d.last_price,
      marketValue: d.market_value,
      dayPnl: d.day_pnl,
      dayPnlPct: pnlPct(d.market_value, d.day_pnl),
      stale: d.stale,
      tick: direction(previous.lastPrice, d.last_price),
    };
  }

  return {
    ...state,
    session: frame.session,
    totalValue: frame.total_value,
    dayPnl: frame.day_pnl,
    dayPnlPct: frame.day_pnl_pct,
    updatedAt: frame.ts,
    positions,
    needsResync,
  };
}

export function applyFrame(
  state: PortfolioState,
  frame: SnapshotFrame | DeltaFrame,
): PortfolioState {
  return frame.type === 'snapshot' ? applySnapshot(state, frame) : applyDelta(state, frame);
}

/** Rows in stable snapshot order - what the FlatList renders. */
export function selectRows(state: PortfolioState): PositionView[] {
  return state.order
    .map((symbol) => state.positions[symbol])
    .filter((row): row is PositionView => row !== undefined);
}

/** Any position priced off a tick older than the budget drags the whole book. */
export function isBookStale(state: PortfolioState): boolean {
  return state.order.some((symbol) => state.positions[symbol]?.stale === true);
}
