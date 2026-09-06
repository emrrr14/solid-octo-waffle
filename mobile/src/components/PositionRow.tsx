import React from 'react';
import {StyleSheet, Text, View} from 'react-native';

import {formatMoney, formatPercent, formatPrice} from '../format';
import {selectPosition, usePortfolioStore} from '../state/portfolioStore';
import {colors, pnlColor, radius, spacing, typography} from '../theme';
import {ValueTicker} from './ValueTicker';

/**
 * One holding.
 *
 * The row subscribes to *its own* slice of the store, so a NVDA tick re-renders
 * the NVDA row and nothing else.  Passing the whole positions map down from the
 * screen would re-render all 30 rows every second - the single most common
 * reason live portfolio screens feel hot and drain battery.
 */
interface Props {
  symbol: string;
  baseCurrency: string;
  flash: boolean;
}

export const PositionRow = React.memo(function PositionRow({
  symbol,
  baseCurrency,
  flash,
}: Props) {
  const position = usePortfolioStore(selectPosition(symbol));
  if (!position) return null;

  return (
    <View style={styles.row}>
      <View style={styles.left}>
        <View style={styles.symbolLine}>
          <Text style={styles.symbol}>{position.symbol}</Text>
          {position.stale ? (
            <View style={styles.staleBadge}>
              {/* An unlabelled grey dot means nothing to a user; the word does. */}
              <Text style={styles.staleText}>kapalı</Text>
            </View>
          ) : null}
        </View>
        <Text style={styles.meta}>
          {position.quantity} × {formatPrice(position.lastPrice, position.currency)}{' '}
          {position.currency}
        </Text>
      </View>

      <View style={styles.right}>
        <ValueTicker
          value={position.marketValue}
          direction={position.tick}
          flash={flash}
          render={(v) => formatMoney(v, baseCurrency)}
          style={styles.value}
        />
        <Text style={[styles.pnl, {color: pnlColor(position.dayPnl)}]}>
          {formatPercent(position.dayPnlPct)}
        </Text>
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    marginBottom: spacing.sm,
  },
  left: {flex: 1},
  right: {alignItems: 'flex-end'},
  symbolLine: {flexDirection: 'row', alignItems: 'center', gap: spacing.sm},
  symbol: {color: colors.text, ...typography.body},
  meta: {color: colors.textMuted, ...typography.caption, marginTop: 2, ...typography.mono},
  value: {...typography.body, color: colors.text},
  pnl: {...typography.caption, ...typography.mono, marginTop: 2},
  staleBadge: {
    backgroundColor: colors.surfaceRaised,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    borderRadius: radius.sm,
  },
  staleText: {color: colors.stale, fontSize: 10, fontWeight: '600'},
});
