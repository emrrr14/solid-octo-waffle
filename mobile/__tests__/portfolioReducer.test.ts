import {
  applyDelta,
  applySnapshot,
  initialPortfolioState,
  isBookStale,
  selectRows,
} from '../src/state/portfolioReducer';
import type {DeltaFrame, SnapshotFrame} from '../src/types';

const snapshot: SnapshotFrame = {
  type: 'snapshot',
  ts: '2026-09-06T13:30:00Z',
  session: 'open',
  base_currency: 'TRY',
  total_value: 1000,
  day_pnl: 50,
  day_pnl_pct: 5.26,
  positions: [
    {
      symbol: 'NVDA',
      quantity: 2,
      last_price: 110,
      price_currency: 'USD',
      fx_rate: 34,
      market_value_base: 600,
      prev_close_base: 560,
      stale: false,
    },
    {
      symbol: 'THYAO.IS',
      quantity: 10,
      last_price: 40,
      price_currency: 'TRY',
      fx_rate: 1,
      market_value_base: 400,
      prev_close_base: 390,
      stale: false,
    },
  ],
};

const delta = (overrides: Partial<DeltaFrame> = {}): DeltaFrame => ({
  type: 'delta',
  ts: '2026-09-06T13:30:01Z',
  session: 'open',
  total_value: 1010,
  day_pnl: 60,
  day_pnl_pct: 6.31,
  positions: [{symbol: 'NVDA', last_price: 112, market_value: 610, day_pnl: 50, stale: false}],
  ...overrides,
});

describe('snapshot', () => {
  it('builds rows in server order with per-position day P&L', () => {
    const state = applySnapshot(initialPortfolioState, snapshot);
    expect(state.order).toEqual(['NVDA', 'THYAO.IS']);
    expect(state.positions.NVDA?.dayPnl).toBe(40);
    expect(state.positions.NVDA?.dayPnlPct).toBeCloseTo((40 / 560) * 100, 6);
    expect(state.hasSnapshot).toBe(true);
  });

  it('never flashes rows on a fresh snapshot', () => {
    const state = applySnapshot(initialPortfolioState, snapshot);
    expect(selectRows(state).every((row) => row.tick === 'flat')).toBe(true);
  });
});

describe('delta', () => {
  it('is ignored before a snapshot arrives', () => {
    expect(applyDelta(initialPortfolioState, delta())).toBe(initialPortfolioState);
  });

  it('merges only the symbols it carries', () => {
    const state = applyDelta(applySnapshot(initialPortfolioState, snapshot), delta());
    expect(state.positions.NVDA?.marketValue).toBe(610);
    expect(state.positions.NVDA?.tick).toBe('up');
    expect(state.positions['THYAO.IS']?.marketValue).toBe(400); // untouched
    expect(state.totalValue).toBe(1010);
  });

  it('marks direction down when the price falls', () => {
    const base = applySnapshot(initialPortfolioState, snapshot);
    const state = applyDelta(
      base,
      delta({positions: [{symbol: 'NVDA', last_price: 105, market_value: 580, day_pnl: 20, stale: false}]}),
    );
    expect(state.positions.NVDA?.tick).toBe('down');
  });

  it('keeps quantity and currency from the snapshot', () => {
    const state = applyDelta(applySnapshot(initialPortfolioState, snapshot), delta());
    expect(state.positions.NVDA?.quantity).toBe(2);
    expect(state.positions.NVDA?.currency).toBe('USD');
  });

  it('flags a resync when it references an unknown symbol', () => {
    const state = applyDelta(
      applySnapshot(initialPortfolioState, snapshot),
      delta({positions: [{symbol: 'AAPL', last_price: 1, market_value: 1, day_pnl: 0, stale: false}]}),
    );
    expect(state.needsResync).toBe(true);
    expect(state.positions.AAPL).toBeUndefined();
  });

  it('propagates the session so the UI can stop animating at the close', () => {
    const state = applyDelta(applySnapshot(initialPortfolioState, snapshot), delta({session: 'closed'}));
    expect(state.session).toBe('closed');
  });
});

describe('staleness', () => {
  it('reports a stale book when any position is stale', () => {
    const stale = {
      ...snapshot,
      positions: [{...snapshot.positions[0]!, stale: true}, snapshot.positions[1]!],
    };
    expect(isBookStale(applySnapshot(initialPortfolioState, stale))).toBe(true);
    expect(isBookStale(applySnapshot(initialPortfolioState, snapshot))).toBe(false);
  });
});
