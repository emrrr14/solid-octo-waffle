/**
 * Zustand store.
 *
 * Why zustand and not Context: at 1 Hz a Context provider re-renders every
 * consumer in the tree, so a 30-row list re-renders 30 rows a second whether or
 * not their numbers changed.  Zustand's selector subscriptions mean a row
 * re-renders only when *its own* slice changes - the difference between a
 * smooth list and a warm phone.
 */

import { create } from 'zustand';

import type { ConnectionStatus, DeltaFrame, ServerFrame, SnapshotFrame } from '../types';
import {
  applyFrame,
  initialPortfolioState,
  type PortfolioState,
} from './portfolioReducer';

interface PortfolioStore {
  data: PortfolioState;
  status: ConnectionStatus;
  error: string | null;
  ingest: (frame: ServerFrame) => void;
  setStatus: (status: ConnectionStatus) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

export const usePortfolioStore = create<PortfolioStore>((set) => ({
  data: initialPortfolioState,
  status: 'idle',
  error: null,

  ingest: (frame) =>
    set((store) => {
      if (frame.type === 'heartbeat') return store;
      return { data: applyFrame(store.data, frame as SnapshotFrame | DeltaFrame) };
    }),

  setStatus: (status) => set({ status }),
  setError: (error) => set({ error }),
  reset: () => set({ data: initialPortfolioState, status: 'idle', error: null }),
}));

/** Selectors - components subscribe to the narrowest slice they render. */
export const selectTotals = (store: PortfolioStore) => ({
  totalValue: store.data.totalValue,
  dayPnl: store.data.dayPnl,
  dayPnlPct: store.data.dayPnlPct,
  currency: store.data.baseCurrency,
  session: store.data.session,
});

export const selectPosition = (symbol: string) => (store: PortfolioStore) =>
  store.data.positions[symbol];

export const selectOrder = (store: PortfolioStore) => store.data.order;
export const selectStatus = (store: PortfolioStore) => store.status;
