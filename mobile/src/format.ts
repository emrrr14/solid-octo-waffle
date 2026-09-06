/**
 * Money and percentage formatting.
 *
 * Hermes ships Intl on iOS, but a currency format that silently falls back to
 * "TRY 1234.5" in one build and "₺1.234,50" in another is the kind of bug that
 * reaches screenshots.  So: try Intl once, cache the formatter, and keep an
 * explicit fallback that produces the Turkish convention (1.234,50 ₺) rather
 * than whatever the engine defaults to.
 */

const SYMBOLS: Record<string, string> = {TRY: '₺', USD: '$', EUR: '€'};

const cache = new Map<string, Intl.NumberFormat>();

function formatter(currency: string, locale: string): Intl.NumberFormat | null {
  const key = `${locale}:${currency}`;
  const hit = cache.get(key);
  if (hit) return hit;
  try {
    const made = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    made.format(1); // some engines only throw on first use
    cache.set(key, made);
    return made;
  } catch {
    return null;
  }
}

function groupTr(value: number, decimals: number): string {
  const fixed = Math.abs(value).toFixed(decimals);
  const [whole = '0', fraction] = fixed.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const body = fraction ? `${grouped},${fraction}` : grouped;
  return value < 0 ? `-${body}` : body;
}

export function formatMoney(value: number, currency: string, locale = 'tr-TR'): string {
  const intl = formatter(currency, locale);
  if (intl) return intl.format(value);
  const symbol = SYMBOLS[currency] ?? currency;
  return `${groupTr(value, 2)} ${symbol}`;
}

/** Always signed: on a P&L line the sign carries the meaning, not the colour. */
export function formatSignedMoney(value: number, currency: string, locale = 'tr-TR'): string {
  const body = formatMoney(Math.abs(value), currency, locale);
  return `${value >= 0 ? '+' : '−'}${body}`;
}

export function formatPercent(value: number, decimals = 2): string {
  return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(decimals)}%`;
}

export function formatWeight(weight: number): string {
  return `${(weight * 100).toFixed(1)}%`;
}

export function formatBps(bps: number): string {
  return `${bps >= 0 ? '+' : '−'}${Math.abs(bps).toFixed(0)}bp`;
}

/** Price precision follows the venue's tick, not a global constant. */
export function formatPrice(value: number, currency: string): string {
  const decimals = currency === 'TRY' ? 2 : Math.abs(value) < 1 ? 4 : 2;
  return groupTr(value, decimals);
}

export function formatClock(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
