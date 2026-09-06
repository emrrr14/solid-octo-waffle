/**
 * REST client.
 *
 * Deliberately thin: a hard timeout on everything (a finance app that spins
 * forever on a flaky train connection is indistinguishable from a broken one),
 * typed responses so a backend schema change fails the typecheck, and exactly
 * one retry - after a forced token refresh on a 401. Nothing else is retried:
 * the confirmation endpoint is safe to replay only because it carries an
 * idempotency key, and a retry loop on a dead session just hammers the API.
 */

import type {SessionLike} from '../auth/session';
import type { AllocationView, MacroEventView } from '../types';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export interface ApiConfig {
  baseUrl: string;
  session: SessionLike;
  timeoutMs?: number;
}

export class ApiClient {
  constructor(private readonly config: ApiConfig) {}

  private async send<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs ?? 10_000);
    try {
      const response = await fetch(`${this.config.baseUrl}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? {Authorization: `Bearer ${token}`} : {}),
          ...(init.headers ?? {}),
        },
      });
      if (!response.ok) {
        throw new ApiError(`${init.method ?? 'GET'} ${path} failed`, response.status);
      }
      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if ((error as Error).name === 'AbortError') {
        throw new ApiError('The request timed out', 408);
      }
      throw new ApiError((error as Error).message, 0);
    } finally {
      clearTimeout(timeout);
    }
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    try {
      return await this.send<T>(path, init, await this.config.session.getAccessToken());
    } catch (error) {
      // One forced refresh, one retry. The access token can lapse between the
      // session's skew check and the server reading it; a single retry hides
      // that, while a retry loop would just hammer a genuinely dead session.
      // The refresh itself is single-flight inside the session, so parallel
      // requests hitting 401 together still rotate the token exactly once.
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
      const refreshed = await this.config.session.getAccessToken(true);
      if (!refreshed) throw error;
      return this.send<T>(path, init, refreshed);
    }
  }

  getAllocation(portfolioId: string): Promise<AllocationView> {
    return this.request<AllocationView>(`/api/portfolios/${portfolioId}/allocation`);
  }

  getMacroEvents(limit = 20): Promise<MacroEventView[]> {
    return this.request<MacroEventView[]>(`/api/macro/events?limit=${limit}`);
  }

  /**
   * Confirm a proposed rebalance.  `idempotencyKey` is the decision id: tapping
   * twice, or retrying after a timeout, must never place two sets of orders.
   */
  confirmRebalance(portfolioId: string, idempotencyKey: string): Promise<{accepted: boolean}> {
    return this.request(`/api/portfolios/${portfolioId}/rebalance/confirm`, {
      method: 'POST',
      headers: {'Idempotency-Key': idempotencyKey},
      body: JSON.stringify({decision_id: idempotencyKey}),
    });
  }
}
