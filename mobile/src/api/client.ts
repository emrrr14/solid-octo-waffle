/**
 * REST client.
 *
 * Deliberately thin: no retries on POST (a duplicated rebalance confirmation is
 * worse than an error message), a hard timeout on everything (a finance app that
 * spins forever on a flaky train connection is indistinguishable from a broken
 * one), and typed responses so a backend schema change fails the typecheck.
 */

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
  getToken: () => Promise<string | null>;
  timeoutMs?: number;
}

export class ApiClient {
  constructor(private readonly config: ApiConfig) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs ?? 10_000);
    const token = await this.config.getToken();
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
