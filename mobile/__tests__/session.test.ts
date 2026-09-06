import {AuthApiError, type TokenPair} from '../src/api/auth';
import {Session} from '../src/auth/session';

class FakeStorage {
  value: string | null = null;
  writes = 0;
  clears = 0;

  async saveSession(token: string) {
    this.value = token;
    this.writes += 1;
    return true;
  }
  async readSession() {
    return this.value;
  }
  async clearSession() {
    this.value = null;
    this.clears += 1;
    return true;
  }
}

function pair(n: number, expiresIn = 900): TokenPair {
  return {
    access_token: `access-${n}`,
    refresh_token: `refresh-${n}`,
    token_type: 'bearer',
    expires_in: expiresIn,
  };
}

function build(options: {expiresIn?: number; refreshImpl?: () => Promise<TokenPair>} = {}) {
  const storage = new FakeStorage();
  const calls = {login: 0, refresh: 0, logout: 0};
  let issued = 0;
  let clock = 1_000_000;

  const api = {
    login: async () => {
      calls.login += 1;
      issued += 1;
      return pair(issued, options.expiresIn ?? 900);
    },
    refresh: options.refreshImpl
      ? async () => {
          calls.refresh += 1;
          return options.refreshImpl!();
        }
      : async () => {
          calls.refresh += 1;
          issued += 1;
          return pair(issued, options.expiresIn ?? 900);
        },
    logout: async () => {
      calls.logout += 1;
    },
  };

  const session = new Session({
    baseUrl: 'http://test',
    storage,
    api: api as never,
    now: () => clock,
  });

  return {session, storage, calls, advance: (ms: number) => (clock += ms)};
}

describe('sign in', () => {
  it('stores only the refresh token', async () => {
    const {session, storage} = build();
    await session.signIn('a@b.com', 'password-1');

    expect(await session.getAccessToken()).toBe('access-1');
    expect(storage.value).toBe('refresh-1'); // access token stays in memory
    expect(session.getState()).toBe('signed-in');
  });

  it('notifies subscribers', async () => {
    const {session} = build();
    const states: string[] = [];
    session.subscribe((s) => states.push(s));
    await session.signIn('a@b.com', 'password-1');
    expect(states).toEqual(['signed-in']);
  });
});

describe('access token lifecycle', () => {
  it('reuses a fresh token without touching the network', async () => {
    const {session, calls} = build();
    await session.signIn('a@b.com', 'password-1');
    await session.getAccessToken();
    await session.getAccessToken();
    expect(calls.refresh).toBe(0);
  });

  it('refreshes before the token actually expires', async () => {
    const {session, calls, advance} = build({expiresIn: 900});
    await session.signIn('a@b.com', 'password-1');

    advance(839_000); // 61s of life left - outside the 60s skew
    await session.getAccessToken();
    expect(calls.refresh).toBe(0);

    advance(2_000); // 59s left - inside the skew
    expect(await session.getAccessToken()).toBe('access-2');
    expect(calls.refresh).toBe(1);
  });

  it('refreshes on demand when forced', async () => {
    const {session, calls} = build();
    await session.signIn('a@b.com', 'password-1');
    expect(await session.getAccessToken(true)).toBe('access-2');
    expect(calls.refresh).toBe(1);
  });
});

describe('single-flight refresh', () => {
  it('collapses concurrent refreshes into one network call', async () => {
    // This is the whole reason the class exists: the server rotates refresh
    // tokens and treats a replay as theft, so two parallel refreshes would
    // revoke the family and sign the user out.
    const {session, calls} = build();
    await session.signIn('a@b.com', 'password-1');

    const results = await Promise.all([
      session.getAccessToken(true),
      session.getAccessToken(true),
      session.getAccessToken(true),
    ]);

    expect(calls.refresh).toBe(1);
    expect(results).toEqual(['access-2', 'access-2', 'access-2']);
  });

  it('allows a later refresh once the first has settled', async () => {
    const {session, calls} = build();
    await session.signIn('a@b.com', 'password-1');
    await session.getAccessToken(true);
    await session.getAccessToken(true);
    expect(calls.refresh).toBe(2);
  });
});

describe('failure handling', () => {
  it('signs out when the refresh token is rejected', async () => {
    const {session, storage} = build({
      refreshImpl: async () => {
        throw new AuthApiError('nope', 401);
      },
    });
    await session.signIn('a@b.com', 'password-1');

    expect(await session.getAccessToken(true)).toBeNull();
    expect(session.getState()).toBe('signed-out');
    expect(storage.value).toBeNull();
  });

  it('keeps the session on a network error', async () => {
    // Being on the underground is not a reason to make someone sign in again.
    const {session, storage} = build({
      refreshImpl: async () => {
        throw new AuthApiError('offline', 0);
      },
    });
    await session.signIn('a@b.com', 'password-1');

    expect(await session.getAccessToken(true)).toBeNull();
    expect(session.getState()).toBe('signed-in');
    expect(storage.value).toBe('refresh-1');
  });
});

describe('restore', () => {
  it('reports signed-out with nothing in storage', async () => {
    const {session, calls} = build();
    expect(await session.restore()).toBe(false);
    expect(session.getState()).toBe('signed-out');
    expect(calls.refresh).toBe(0);
  });

  it('exchanges a stored refresh token for an access token', async () => {
    const {session, storage} = build();
    storage.value = 'refresh-from-keychain';

    expect(await session.restore()).toBe(true);
    expect(session.getState()).toBe('signed-in');
    expect(await session.getAccessToken()).toBe('access-1');
  });
});

describe('sign out', () => {
  it('clears local state before the network call', async () => {
    const {session, storage, calls} = build();
    await session.signIn('a@b.com', 'password-1');
    await session.signOut();

    expect(session.getState()).toBe('signed-out');
    expect(storage.value).toBeNull();
    expect(storage.clears).toBe(1);
    expect(calls.logout).toBe(1);
    expect(await session.getAccessToken()).toBeNull();
  });

  it('still signs out locally when the server call fails', async () => {
    const {session, storage} = build();
    await session.signIn('a@b.com', 'password-1');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (session as any).api = undefined;
    await session.signOut();
    expect(session.getState()).toBe('signed-out');
    expect(storage.value).toBeNull();
  });
});
