"""TEFAS (mutual funds) and BES (pension funds) end-of-day ingestion.

TEFAS publishes no documented public API; the platform's own UI calls
``POST /api/DB/BindHistoryInfo`` with a form body, and that is what everyone -
including the widely used ``tefasfunds``/``tefas-crawler`` packages - uses.
Treat it accordingly:

* ``fontip="YAT"`` -> mutual funds, ``fontip="EMK"`` -> BES pension funds.
* Prices land **after the close**, typically the evening of T for T's NAV, and
  late/revised rows happen.  The job must be idempotent (upsert on
  ``(fund_code, date)``), not append-only.
* Rate-limit yourself and cache aggressively.  This is a courtesy endpoint; if
  the product depends on it, buy a licensed data feed (Rasyonet, Foreks,
  Matriks) before launch and keep this as the fallback.

BES funds are also on TEFAS under ``EMK``; EGM (Emeklilik Gözetim Merkezi)
publishes the same NAVs and is the better cross-check for reconciliation.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import httpx
import pandas as pd

log = logging.getLogger(__name__)

TEFAS_URL = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.tefas.gov.tr",
    "Referer": "https://www.tefas.gov.tr/TarihselVeriler.aspx",
    "User-Agent": "robo-advisor/1.0 (+contact@example.com)",
}
MAX_WINDOW = timedelta(days=90)     # the endpoint truncates longer ranges


class TefasClient:
    def __init__(self, client: httpx.AsyncClient | None = None, rate_limit_s: float = 1.0) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0, headers=HEADERS)
        self._rate_limit = rate_limit_s

    async def _fetch_window(self, start: date, end: date, fontip: str, codes: str) -> list[dict]:
        payload = {
            "fontip": fontip,
            "bastarih": start.strftime("%d.%m.%Y"),
            "bittarih": end.strftime("%d.%m.%Y"),
            "fonkod": codes,
        }
        for attempt in range(4):
            try:
                resp = await self._client.post(TEFAS_URL, data=payload)
                resp.raise_for_status()
                return resp.json().get("data", [])
            except (httpx.HTTPError, ValueError):
                wait = 2 ** attempt
                log.warning("tefas: %s-%s attempt %d failed, retrying in %ds",
                            start, end, attempt + 1, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(f"tefas: giving up on {start}..{end}")

    async def history(
        self,
        start: date,
        end: date,
        fund_codes: list[str] | None = None,
        fontip: str = "YAT",
    ) -> pd.DataFrame:
        """Daily NAV history.  Long ranges are chunked into <=90-day windows."""
        codes = ",".join(fund_codes) if fund_codes else ""
        rows: list[dict] = []
        cursor = start
        while cursor <= end:
            window_end = min(cursor + MAX_WINDOW, end)
            rows.extend(await self._fetch_window(cursor, window_end, fontip, codes))
            cursor = window_end + timedelta(days=1)
            await asyncio.sleep(self._rate_limit)

        if not rows:
            return pd.DataFrame(columns=["date", "code", "title", "nav", "shares", "investors"])

        df = pd.DataFrame(rows)
        out = pd.DataFrame(
            {
                # TARIH is epoch milliseconds, not a date string.
                "date": pd.to_datetime(df["TARIH"], unit="ms").dt.normalize(),
                "code": df["FONKODU"],
                "title": df.get("FONUNVAN"),
                "nav": pd.to_numeric(df["FIYAT"], errors="coerce"),
                "shares": pd.to_numeric(df.get("TEDPAYSAYISI"), errors="coerce"),
                "investors": pd.to_numeric(df.get("KISISAYISI"), errors="coerce"),
            }
        )
        return out.dropna(subset=["nav"]).drop_duplicates(["date", "code"]).sort_values(["code", "date"])

    async def pension_history(self, start: date, end: date, fund_codes: list[str] | None = None):
        """BES / pension funds."""
        return await self.history(start, end, fund_codes, fontip="EMK")


def nav_to_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Wide daily return matrix (index=date, columns=fund code).

    Log returns: they add across time, which is what the factor model assumes,
    and they keep a 40% single-day TRY move from distorting the fit the way a
    simple return would.
    """
    import numpy as np

    wide = df.pivot(index="date", columns="code", values="nav").sort_index()
    # Funds do not price on non-business days; forward-fill at most one gap so a
    # single missing publication does not create a fake -100% return.
    wide = wide.ffill(limit=1)
    return np.log(wide).diff().dropna(how="all")
