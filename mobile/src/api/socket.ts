/**
 * Portfolio websocket client.
 *
 * Everything here exists because of how phones behave, not how sockets behave:
 *
 * - **A dead socket looks alive.**  Walking from wifi to cellular leaves iOS
 *   holding a TCP connection that will never deliver another byte and never
 *   fire `onclose`.  The only reliable detector is a watchdog on *inbound*
 *   frames - the server heartbeats every 15s, so silence past `watchdogMs`
 *   means reconnect, whatever the socket says its state is.
 * - **Reconnect storms are self-inflicted outages.**  Exponential backoff with
 *   jitter, so 50k phones coming out of a tunnel don't arrive together.
 * - **An expired token is not a dead session.**  The server closes with 4401
 *   when the access token lapses mid-stream (it will, on a socket held open for
 *   hours).  One forced refresh and a reconnect fixes that invisibly; a second
 *   4401 without an intervening frame means the session really is gone, and
 *   only then do we stop and surface it.  Retrying a rejected token forever is
 *   how you get rate-limited by your own gateway.
 *
 * The WebSocket constructor and timers are injected so the whole state machine
 * runs under Jest in Node with no React Native runtime.
 */

import type { ServerFrame } from '../types';

export interface SocketLike {
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  close(code?: number, reason?: string): void;
  send(data: string): void;
}

export type SocketFactory = (url: string, protocols?: string[]) => SocketLike;

export interface PortfolioSocketOptions {
  url: string;
  portfolioId: string;
  /** `force` asks the session to refresh before answering (after a 4401). */
  getToken: (force?: boolean) => Promise<string | null>;
  onFrame: (frame: ServerFrame) => void;
  onStatus: (status: 'connecting' | 'live' | 'reconnecting' | 'offline') => void;
  onFatal?: (reason: string) => void;
  socketFactory?: SocketFactory;
  /** Reconnect if no inbound frame arrives for this long (server beats every 15s). */
  watchdogMs?: number;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
  random?: () => number;
  setTimeoutFn?: (fn: () => void, ms: number) => unknown;
  clearTimeoutFn?: (handle: unknown) => void;
}

/** Close codes the server uses deliberately. 4401 is recoverable once. */
const UNAUTHORIZED = 4401;

const FATAL_CODES: Record<number, string> = {
  4403: 'This portfolio is not available on your account',
  4404: 'Portfolio not found',
};

const SESSION_EXPIRED = 'Session expired - please sign in again';

export function backoffDelay(
  attempt: number,
  baseMs: number,
  maxMs: number,
  random: () => number,
): number {
  const ceiling = Math.min(baseMs * 2 ** attempt, maxMs);
  // Full jitter: uniform in [ceiling/2, ceiling).  Halving the floor keeps the
  // reconnect responsive; the jitter keeps a fleet of phones from syncing up.
  return Math.round(ceiling * (0.5 + 0.5 * random()));
}

export class PortfolioSocket {
  private readonly opts: Required<
    Pick<PortfolioSocketOptions, 'watchdogMs' | 'baseBackoffMs' | 'maxBackoffMs' | 'random'>
  > &
    PortfolioSocketOptions;

  private socket: SocketLike | null = null;
  private attempt = 0;
  /** 4401s since the last frame we successfully received. */
  private reauthAttempts = 0;
  private closedByUs = false;
  private reconnectHandle: unknown = null;
  private watchdogHandle: unknown = null;
  private readonly setTimeoutFn: (fn: () => void, ms: number) => unknown;
  private readonly clearTimeoutFn: (handle: unknown) => void;
  private readonly makeSocket: SocketFactory;

  constructor(options: PortfolioSocketOptions) {
    this.opts = {
      watchdogMs: 45_000,
      baseBackoffMs: 1_000,
      maxBackoffMs: 30_000,
      random: Math.random,
      ...options,
    };
    this.setTimeoutFn = options.setTimeoutFn ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimeoutFn = options.clearTimeoutFn ?? ((h) => clearTimeout(h as never));
    this.makeSocket =
      options.socketFactory ??
      ((url, protocols) => new WebSocket(url, protocols) as unknown as SocketLike);
  }

  async connect(force = false): Promise<void> {
    this.closedByUs = false;
    const token = await this.opts.getToken(force);
    if (!token) {
      // A forced fetch returning nothing means the refresh token is gone too.
      this.fail(force ? SESSION_EXPIRED : 'Not signed in');
      return;
    }

    this.opts.onStatus(this.attempt === 0 ? 'connecting' : 'reconnecting');

    const url = `${this.opts.url}/ws/portfolio/${encodeURIComponent(this.opts.portfolioId)}`;
    // The token rides in a subprotocol header, never the query string: URLs end
    // up in proxy logs and crash reports.
    const socket = this.makeSocket(url, ['bearer', token]);
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.opts.onStatus('live');
      this.armWatchdog();
    };

    socket.onmessage = (event) => {
      this.armWatchdog();
      // A frame proves the credential worked; the next 4401 is a fresh problem.
      this.reauthAttempts = 0;
      let frame: ServerFrame;
      try {
        frame = JSON.parse(event.data) as ServerFrame;
      } catch {
        return; // a malformed frame is a server bug, not a reason to drop the stream
      }
      if (frame.type === 'heartbeat') return; // liveness only; nothing to render
      this.opts.onFrame(frame);
    };

    socket.onerror = () => {
      // onerror is always followed by onclose in every RN WebSocket impl;
      // reconnecting here as well would double the attempt counter.
    };

    socket.onclose = (event) => {
      this.clearWatchdog();
      this.socket = null;
      if (this.closedByUs) return;

      const fatal = FATAL_CODES[event.code];
      if (fatal) {
        this.fail(fatal);
        return;
      }

      if (event.code === UNAUTHORIZED) {
        void this.reauthenticate();
        return;
      }

      this.scheduleReconnect();
    };
  }

  /** Explicit teardown: backgrounding, sign-out, screen unmount. */
  close(): void {
    this.closedByUs = true;
    this.clearWatchdog();
    if (this.reconnectHandle !== null) {
      this.clearTimeoutFn(this.reconnectHandle);
      this.reconnectHandle = null;
    }
    this.socket?.close(1000, 'client closed');
    this.socket = null;
    this.attempt = 0;
    this.reauthAttempts = 0;
    this.opts.onStatus('offline');
  }

  /**
   * The access token lapsed mid-stream. Force a refresh and reconnect once; if
   * that fails, or a second 4401 arrives with no frame in between, the session
   * is genuinely gone and the user has to sign in.
   */
  private async reauthenticate(): Promise<void> {
    this.reauthAttempts += 1;
    if (this.reauthAttempts > 1) {
      this.fail(SESSION_EXPIRED);
      return;
    }
    this.opts.onStatus('reconnecting');
    // connect(true) does the forced refresh itself - fetching the token here as
    // well would rotate it twice and hand the second socket a stale one.
    await this.connect(true);
  }

  private scheduleReconnect(): void {
    const delay = backoffDelay(
      this.attempt,
      this.opts.baseBackoffMs,
      this.opts.maxBackoffMs,
      this.opts.random,
    );
    this.attempt += 1;
    this.opts.onStatus('reconnecting');
    this.reconnectHandle = this.setTimeoutFn(() => {
      this.reconnectHandle = null;
      void this.connect();
    }, delay);
  }

  private armWatchdog(): void {
    this.clearWatchdog();
    this.watchdogHandle = this.setTimeoutFn(() => {
      // Silence past the budget: assume a half-open socket and start over.
      this.socket?.close(4000, 'watchdog');
      this.socket = null;
      this.scheduleReconnect();
    }, this.opts.watchdogMs);
  }

  private clearWatchdog(): void {
    if (this.watchdogHandle !== null) {
      this.clearTimeoutFn(this.watchdogHandle);
      this.watchdogHandle = null;
    }
  }

  private fail(reason: string): void {
    this.closedByUs = true;
    this.clearWatchdog();
    this.opts.onStatus('offline');
    this.opts.onFatal?.(reason);
  }
}
