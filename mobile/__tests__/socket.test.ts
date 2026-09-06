import {PortfolioSocket, backoffDelay, type SocketLike} from '../src/api/socket';
import type {ServerFrame} from '../src/types';

/** Controllable fake for the platform WebSocket. */
class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: {data: string}) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: {code: number; reason?: string}) => void) | null = null;
  closed: {code?: number} | null = null;
  sent: string[] = [];

  constructor(
    readonly url: string,
    readonly protocols?: string[],
  ) {
    FakeSocket.instances.push(this);
  }

  close(code?: number) {
    this.closed = {code};
  }

  send(data: string) {
    this.sent.push(data);
  }
}

/** Deterministic timer queue: nothing here waits on real time. */
class FakeClock {
  private handle = 0;
  private tasks = new Map<number, {fn: () => void; due: number}>();
  now = 0;

  setTimeout = (fn: () => void, ms: number): unknown => {
    const id = ++this.handle;
    this.tasks.set(id, {fn, due: this.now + ms});
    return id;
  };

  clearTimeout = (h: unknown): void => {
    this.tasks.delete(h as number);
  };

  advance(ms: number): void {
    this.now += ms;
    for (const [id, task] of [...this.tasks]) {
      if (task.due <= this.now) {
        this.tasks.delete(id);
        task.fn();
      }
    }
  }

  get pending(): number {
    return this.tasks.size;
  }
}

function build(overrides: Partial<Parameters<typeof makeOptions>[0]> = {}) {
  const clock = new FakeClock();
  const frames: ServerFrame[] = [];
  const statuses: string[] = [];
  const fatals: string[] = [];
  FakeSocket.instances = [];

  const socket = new PortfolioSocket({
    url: 'ws://localhost:8000',
    portfolioId: 'p1',
    getToken: async () => 'tok',
    onFrame: (f) => frames.push(f),
    onStatus: (s) => statuses.push(s),
    onFatal: (r) => fatals.push(r),
    socketFactory: (url, protocols) => new FakeSocket(url, protocols),
    setTimeoutFn: clock.setTimeout,
    clearTimeoutFn: clock.clearTimeout,
    random: () => 0.5,
    baseBackoffMs: 1000,
    maxBackoffMs: 30000,
    watchdogMs: 45000,
    ...overrides,
  });

  return {socket, clock, frames, statuses, fatals, sockets: FakeSocket.instances};
}

// Only used for the type of `build`'s overrides.
function makeOptions(o: ConstructorParameters<typeof PortfolioSocket>[0]) {
  return o;
}

/** Let the reconnect path's `await getToken()` microtask settle. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const snapshotFrame = JSON.stringify({
  type: 'snapshot',
  ts: '2026-09-06T13:30:00Z',
  session: 'open',
  base_currency: 'TRY',
  total_value: 100,
  day_pnl: 1,
  day_pnl_pct: 1,
  positions: [],
});

describe('backoffDelay', () => {
  it('grows exponentially and saturates at the ceiling', () => {
    expect(backoffDelay(0, 1000, 30000, () => 0)).toBe(500);
    expect(backoffDelay(1, 1000, 30000, () => 0)).toBe(1000);
    expect(backoffDelay(10, 1000, 30000, () => 1)).toBe(30000);
  });

  it('jitters within [ceiling/2, ceiling]', () => {
    for (const r of [0, 0.25, 0.5, 0.99]) {
      const delay = backoffDelay(3, 1000, 30000, () => r);
      expect(delay).toBeGreaterThanOrEqual(4000);
      expect(delay).toBeLessThanOrEqual(8000);
    }
  });
});

describe('PortfolioSocket', () => {
  it('sends the token as a subprotocol, never in the URL', async () => {
    const {socket, sockets} = build();
    await socket.connect();
    expect(sockets[0]!.url).toBe('ws://localhost:8000/ws/portfolio/p1');
    expect(sockets[0]!.url).not.toContain('tok');
    expect(sockets[0]!.protocols).toEqual(['bearer', 'tok']);
  });

  it('reports live on open and forwards frames', async () => {
    const {socket, sockets, statuses, frames} = build();
    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onmessage?.({data: snapshotFrame});
    expect(statuses).toEqual(['connecting', 'live']);
    expect(frames).toHaveLength(1);
  });

  it('swallows heartbeats and malformed frames', async () => {
    const {socket, sockets, frames} = build();
    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onmessage?.({data: JSON.stringify({type: 'heartbeat', ts: 'x', session: 'open'})});
    sockets[0]!.onmessage?.({data: 'not json'});
    expect(frames).toHaveLength(0);
  });

  it('reconnects with backoff after an unexpected close', async () => {
    const {socket, sockets, clock, statuses} = build();
    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onclose?.({code: 1006});

    expect(statuses).toContain('reconnecting');
    expect(sockets).toHaveLength(1);
    clock.advance(1000); // 1000 * 2^0 * (0.5 + 0.5*0.5) = 750ms
    await flush();
    expect(sockets).toHaveLength(2);
  });

  it('refreshes the token and reconnects after a 4401', async () => {
    const forced: boolean[] = [];
    const tokens = ['tok-1', 'tok-2'];
    const {socket, sockets, fatals, statuses} = build({
      getToken: async (force?: boolean) => {
        forced.push(force === true);
        return tokens.shift() ?? null;
      },
    });

    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onclose?.({code: 4401}); // access token lapsed mid-stream
    await flush();

    expect(forced).toEqual([false, true]); // second call forced a refresh
    expect(sockets).toHaveLength(2);
    expect(sockets[1]!.protocols).toEqual(['bearer', 'tok-2']);
    expect(statuses).toContain('reconnecting');
    expect(fatals).toEqual([]); // the user never saw this happen
  });

  it('gives up when the refresh itself fails', async () => {
    const {socket, sockets, fatals} = build({
      getToken: async (force?: boolean) => (force ? null : 'tok-1'),
    });

    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onclose?.({code: 4401});
    await flush();

    expect(fatals).toEqual(['Session expired - please sign in again']);
    expect(sockets).toHaveLength(1);
  });

  it('gives up on a second 4401 with no frame in between', async () => {
    const {socket, sockets, fatals} = build({getToken: async () => 'tok'});

    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onclose?.({code: 4401});
    await flush();
    sockets[1]!.onopen?.();
    sockets[1]!.onclose?.({code: 4401}); // refreshed token rejected too
    await flush();

    expect(fatals).toEqual(['Session expired - please sign in again']);
    expect(sockets).toHaveLength(2);
  });

  it('a delivered frame clears the reauth budget', async () => {
    const {socket, sockets, fatals} = build({getToken: async () => 'tok'});

    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onclose?.({code: 4401});
    await flush();

    sockets[1]!.onopen?.();
    sockets[1]!.onmessage?.({data: snapshotFrame}); // stream is healthy again
    sockets[1]!.onclose?.({code: 4401});           // hours later, token lapses again
    await flush();

    expect(fatals).toEqual([]);
    expect(sockets).toHaveLength(3);
  });

  it('stops for good on a forbidden portfolio', async () => {
    const {socket, sockets, clock, fatals} = build();
    await socket.connect();
    sockets[0]!.onopen?.();
    sockets[0]!.onclose?.({code: 4404});

    expect(fatals).toEqual(['Portfolio not found']);
    clock.advance(60_000);
    await flush();
    expect(sockets).toHaveLength(1); // never retried
  });

  it('reconnects when the stream goes silent (half-open socket)', async () => {
    const {socket, sockets, clock} = build();
    await socket.connect();
    sockets[0]!.onopen?.();

    clock.advance(44_000);
    expect(sockets).toHaveLength(1); // still within the budget

    clock.advance(2_000); // watchdog fires
    expect(sockets[0]!.closed?.code).toBe(4000);
    clock.advance(1_000); // backoff elapses
    await flush();
    expect(sockets).toHaveLength(2);
  });

  it('each inbound frame re-arms the watchdog', async () => {
    const {socket, sockets, clock} = build();
    await socket.connect();
    sockets[0]!.onopen?.();

    for (let i = 0; i < 5; i++) {
      clock.advance(30_000);
      sockets[0]!.onmessage?.({data: snapshotFrame});
    }
    expect(sockets).toHaveLength(1); // 150s of traffic, no reconnect
  });

  it('close() is final and cancels pending work', async () => {
    const {socket, sockets, clock, statuses} = build();
    await socket.connect();
    sockets[0]!.onopen?.();
    socket.close();

    expect(statuses[statuses.length - 1]).toBe('offline');
    expect(clock.pending).toBe(0);
    sockets[0]!.onclose?.({code: 1000});
    clock.advance(60_000);
    await flush();
    expect(sockets).toHaveLength(1);
  });

  it('fails cleanly when there is no session token', async () => {
    const {socket, fatals, sockets} = build({getToken: async () => null});
    await socket.connect();
    expect(fatals).toEqual(['Not signed in']);
    expect(sockets).toHaveLength(0);
  });
});
