/**
 * Session manager: the single owner of tokens on the device.
 *
 * The one rule that shapes this file: **refresh is single-flight.** The server
 * rotates refresh tokens and treats a replay as theft - it revokes the whole
 * family. If the websocket and two REST calls all notice an expired access
 * token at the same moment and each refreshes, the second and third present an
 * already-used token, the family is revoked, and the user is silently signed
 * out. So concurrent callers share one in-flight refresh.
 *
 * The storage split matches the tokens' lifetimes: the refresh token goes to
 * the Keychain (long-lived, must survive a restart), the access token stays in
 * memory only (15 minutes, worthless to persist, one less secret on disk).
 */

import {authApi, AuthApiError, type TokenPair} from '../api/auth';
import {SecureVault} from '../native/SecureVault';

export type AuthState = 'unknown' | 'signed-out' | 'signed-in';

interface Storage {
  saveSession(token: string): Promise<boolean>;
  readSession(): Promise<string | null>;
  clearSession(): Promise<boolean>;
}

export interface SessionOptions {
  baseUrl: string;
  storage?: Storage;
  api?: typeof authApi;
  /** Refresh this long before the access token actually expires. */
  skewMs?: number;
  now?: () => number;
}

export class Session {
  private accessToken: string | null = null;
  private accessExpiresAt = 0;
  private refreshToken: string | null = null;
  private inFlight: Promise<string | null> | null = null;
  private state: AuthState = 'unknown';
  private readonly listeners = new Set<(state: AuthState) => void>();

  private readonly storage: Storage;
  private readonly api: typeof authApi;
  private readonly skewMs: number;
  private readonly now: () => number;

  constructor(private readonly options: SessionOptions) {
    this.storage = options.storage ?? SecureVault;
    this.api = options.api ?? authApi;
    this.skewMs = options.skewMs ?? 60_000;
    this.now = options.now ?? Date.now;
  }

  getState(): AuthState {
    return this.state;
  }

  subscribe(listener: (state: AuthState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private setState(next: AuthState): void {
    if (this.state === next) return;
    this.state = next;
    for (const listener of this.listeners) listener(next);
  }

  private adopt(pair: TokenPair): string {
    this.accessToken = pair.access_token;
    this.accessExpiresAt = this.now() + pair.expires_in * 1000;
    this.refreshToken = pair.refresh_token;
    this.setState('signed-in');
    return pair.access_token;
  }

  /** Cold start: is there a usable session on this device? */
  async restore(): Promise<boolean> {
    const stored = await this.storage.readSession();
    if (!stored) {
      this.setState('signed-out');
      return false;
    }
    this.refreshToken = stored;
    const token = await this.getAccessToken(true);
    return token !== null;
  }

  async signIn(email: string, password: string, device?: string): Promise<void> {
    const pair = await this.api.login(this.options.baseUrl, email, password, device);
    this.adopt(pair);
    await this.storage.saveSession(pair.refresh_token);
  }

  async signOut(): Promise<void> {
    const token = this.refreshToken;
    this.accessToken = null;
    this.accessExpiresAt = 0;
    this.refreshToken = null;
    await this.storage.clearSession();
    this.setState('signed-out');
    if (token) {
      // Best effort: local state is already cleared, so a failed round trip
      // must not leave the user looking signed in.
      try {
        await this.api.logout(this.options.baseUrl, token);
      } catch {
        /* ignore */
      }
    }
  }

  /**
   * The access token every caller uses. Refreshes when the current one has
   * expired, is about to, or when `force` is set (a 401 or a 4401 close came
   * back from the server).
   */
  async getAccessToken(force = false): Promise<string | null> {
    const fresh = this.accessToken !== null && this.now() + this.skewMs < this.accessExpiresAt;
    if (fresh && !force) return this.accessToken;
    if (!this.refreshToken) {
      this.setState('signed-out');
      return null;
    }
    if (this.inFlight) return this.inFlight; // join the refresh already running

    this.inFlight = this.performRefresh().finally(() => {
      this.inFlight = null;
    });
    return this.inFlight;
  }

  private async performRefresh(): Promise<string | null> {
    const presented = this.refreshToken;
    if (!presented) return null;
    try {
      return this.adopt(await this.api.refresh(this.options.baseUrl, presented));
    } catch (error) {
      // 401 means the token is gone for good (expired, revoked, or a replay
      // tripped the family kill-switch) - sign out. A network error is not
      // grounds for throwing the session away; the caller can retry.
      if (error instanceof AuthApiError && error.status === 401) {
        this.accessToken = null;
        this.refreshToken = null;
        await this.storage.clearSession();
        this.setState('signed-out');
      }
      return null;
    }
  }
}

/** App-wide instance, created in `src/session.ts` and injected everywhere else. */
export type SessionLike = Pick<Session, 'getAccessToken' | 'getState' | 'subscribe' | 'signIn' | 'signOut' | 'restore'>;
