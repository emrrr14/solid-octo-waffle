/**
 * Unauthenticated auth endpoints.
 *
 * Separate from `ApiClient` on purpose: that client asks the session for a
 * token, and the session calls these - routing them through it would be a
 * refresh loop waiting to happen.
 */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export class AuthApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function post<T>(baseUrl: string, path: string, body: unknown, timeoutMs = 10_000): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method: 'POST',
      signal: controller.signal,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new AuthApiError(
        response.status === 401 ? 'E-posta veya şifre hatalı' : 'Sunucuya ulaşılamadı',
        response.status,
      );
    }
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  } catch (error) {
    if (error instanceof AuthApiError) throw error;
    if ((error as Error).name === 'AbortError') throw new AuthApiError('Bağlantı zaman aşımı', 408);
    throw new AuthApiError((error as Error).message, 0);
  } finally {
    clearTimeout(timeout);
  }
}

export const authApi = {
  login: (baseUrl: string, email: string, password: string, device?: string) =>
    post<TokenPair>(baseUrl, '/api/auth/login', {email, password, device}),

  refresh: (baseUrl: string, refreshToken: string) =>
    post<TokenPair>(baseUrl, '/api/auth/refresh', {refresh_token: refreshToken}),

  logout: (baseUrl: string, refreshToken: string) =>
    post<void>(baseUrl, '/api/auth/logout', {refresh_token: refreshToken}),
};
