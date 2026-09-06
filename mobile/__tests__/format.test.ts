import {
  formatBps,
  formatMoney,
  formatPercent,
  formatPrice,
  formatSignedMoney,
  formatWeight,
  formatClock,
} from '../src/format';

describe('money', () => {
  it('formats TRY and USD with two decimals', () => {
    expect(formatMoney(1234.5, 'TRY')).toMatch(/1[.,]234[.,]50/);
    expect(formatMoney(1234.5, 'USD')).toMatch(/1[.,]234[.,]50/);
  });

  it('always signs P&L', () => {
    expect(formatSignedMoney(12.3, 'TRY').startsWith('+')).toBe(true);
    expect(formatSignedMoney(-12.3, 'TRY').startsWith('−')).toBe(true);
  });
});

describe('percent and weights', () => {
  it('signs percentages so colour is never the only cue', () => {
    expect(formatPercent(2.345)).toBe('+2.35%');
    expect(formatPercent(-2.345)).toBe('−2.35%');
    expect(formatPercent(0)).toBe('+0.00%');
  });

  it('renders weights and basis points', () => {
    expect(formatWeight(0.0612)).toBe('6.1%');
    expect(formatBps(-25)).toBe('−25bp');
    expect(formatBps(0)).toBe('+0bp');
  });
});

describe('prices', () => {
  it('uses more precision for sub-unit foreign prices', () => {
    expect(formatPrice(0.9123, 'USD')).toBe('0,9123');
    expect(formatPrice(0.9123, 'TRY')).toBe('0,91');
    expect(formatPrice(1234.5, 'TRY')).toBe('1.234,50');
  });
});

describe('clock', () => {
  it('degrades to a dash rather than NaN', () => {
    expect(formatClock(null)).toBe('—');
    expect(formatClock('nonsense')).toBe('—');
    expect(formatClock('2026-09-06T13:30:05Z')).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
