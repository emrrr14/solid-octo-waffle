# Data providers

Pricing tiers and product names change; treat every figure here as "verify before
you commit" and the *shape* of the advice as the durable part.

## US equities — NASDAQ / NYSE (per-second streaming)

| Provider | Streaming | Notes |
|---|---|---|
| **Finnhub** | WS, trade-level | Cheapest credible real-time US feed; simple protocol. Good first choice for an MVP. |
| **Polygon.io** | WS, full SIP | The one to grow into: full market coverage, aggregates, corporate actions, generous history. Costs meaningfully more. |
| **Alpaca** | WS | Free tier is **IEX only** (~2% of volume — fine for a demo, misleading for valuation). Paid tier is full SIP; brokerage integration if you ever route orders. |
| **Databento** | WS/raw | Professional, pay-per-usage, exchange-native schemas. Overkill until you care about microstructure. |
| **Twelve Data / Tiingo** | WS | Convenient multi-asset coverage including FX; check redistribution terms carefully. |

**Recommendation:** start on Finnhub, keep the provider behind the
`MarketDataProvider` protocol (`backend/app/market/providers.py`), migrate to
Polygon when coverage or history forces it. The adapter is ~40 lines; nothing
downstream changes.

**S&P 500 the index is licensed by S&P DJI and is not in a retail feed.** Stream
`SPY` or `VOO` (an ETF is a tradable instrument, freely quoted) and label it
honestly in the UI. Same for BIST100: stream `XU100` if your vendor licenses it,
otherwise a BIST100 fund/ETF.

## BIST (Türkiye)

There is no cheap, official, real-time BIST websocket. The honest options:

| Route | Reality |
|---|---|
| **Foreks / Matriks / Rasyonet / İnfina** | The commercial Turkish market-data vendors. Real-time BIST requires a Borsa İstanbul data licence, which they resell. This is the production answer. |
| **Broker APIs** (İş Yatırım TradeMaster, Midas, Garanti) | Real-time for *their* customers, tied to an account, usually not redistributable. |
| **15-minute delayed feeds** | Widely available and cheap. Legitimate for a free tier if the UI says "15 dk gecikmeli" — many Turkish apps ship exactly this. |

`PollingProvider` in `providers.py` exists for this case: vendors that offer only
a REST snapshot become a tick stream that looks identical downstream, emitting
only on price change so the rest of the system cannot tell.

## TEFAS and BES (end-of-day)

* **TEFAS** — `POST https://www.tefas.gov.tr/api/DB/BindHistoryInfo`,
  `fontip="YAT"` (mutual funds) / `"EMK"` (BES pension funds). Undocumented UI
  endpoint; `tefas-crawler` / `tefasfunds` wrap the same call. Implemented in
  `backend/app/tefas/client.py` with ≤90-day windows, retries and rate limiting.
* **EGM (Emeklilik Gözetim Merkezi)** — publishes BES fund data; use it as the
  reconciliation source against TEFAS `EMK` rows.
* **Licensed alternative** — Rasyonet, Foreks and Matriks all sell clean fund
  data with an SLA. Move to one before launch; keep the scraper as fallback and
  alert when the two disagree.

NAVs arrive **after** the close (usually the evening of T for T's NAV) and late
or revised rows are normal. The ingest job upserts on `(fund_code, date)` — never
append-only.

## Macro

| Series | Source | Access |
|---|---|---|
| FED target range (`DFEDTARU`), EFFR, US CPI | **FRED** (St. Louis Fed) | Free API key, JSON. The trigger source in `backend/app/macro/fed.py`. |
| Market-implied policy path | CME **FedWatch** / fed funds futures | No official free API. Scrape FedWatch, or take CME futures from a data vendor. This is what turns a *change* into a *surprise*, so it is worth paying for. |
| TCMB policy rate, TR CPI, USDTRY reference | **TCMB EVDS** | Free API key; the official TR macro warehouse. |
| TR inflation detail | **TÜİK** | Monthly releases, scheduled calendar. |
| FOMC calendar | federalreserve.gov | Static; schedule the poller densely around decision times (18:00 UTC) rather than polling hard all month. |

## FX

Two different rates, do not conflate them:

* **Live USDTRY** for intraday valuation — from your equity vendor or an FX feed.
  Flows through the same `LastPriceCache` as equities.
* **TCMB reference rate** for accounting, statements and any figure a customer
  might reconcile against a bank. Published once a day; store it, don't compute it.

## A starting configuration

| Layer | MVP | Production |
|---|---|---|
| US equities | Finnhub | Polygon.io |
| BIST | 15-min delayed vendor feed | Foreks / Matriks (licensed real-time) |
| TEFAS/BES | `BindHistoryInfo` scraper | Rasyonet / Foreks + scraper as fallback |
| Macro | FRED + EVDS (free) | + CME futures for the surprise term |
| FX | vendor USDTRY | vendor + TCMB reference |

The MVP column costs tens of dollars a month. The production column is where the
real-time **display licence** for each venue lands — that, not compute, is the
line item that will surprise you.
