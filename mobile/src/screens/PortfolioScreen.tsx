import {useCallback, useState} from 'react';
import {FlatList, StyleSheet, Text, View, useWindowDimensions} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';

import {ValueTicker} from '../components/ValueTicker';
import {PositionRow} from '../components/PositionRow';
import {Sparkline} from '../components/Sparkline';
import {StatusBar} from '../components/StatusBar';
import {config} from '../config';
import {formatMoney, formatPercent, formatSignedMoney} from '../format';
import {usePortfolioStream} from '../hooks/usePortfolioStream';
import {useValueHistory} from '../hooks/useValueHistory';
import {usePortfolioStore} from '../state/portfolioStore';
import {colors, pnlColor, spacing, typography} from '../theme';

/**
 * Live portfolio.
 *
 * Rendering rules that keep a 1 Hz list smooth on an iPhone:
 *
 * - The list's `data` is the **symbol array**, not the position objects.  Its
 *   identity changes only when holdings change, so FlatList does no diffing
 *   work per tick; each row pulls its own slice from the store.
 * - Flashes are switched off while the user is dragging.  Animation under a
 *   moving finger competes with the scroll for the UI thread and reads as jank.
 * - Totals come from the server frame.  The app never sums the rows itself -
 *   two sources of truth for one number is how a UI ends up disagreeing with
 *   the statement.
 */
export function PortfolioScreen() {
  usePortfolioStream(config.portfolioId, config.wsUrl);

  const {width} = useWindowDimensions();
  const [scrolling, setScrolling] = useState(false);

  const order = usePortfolioStore((s) => s.data.order);
  const totalValue = usePortfolioStore((s) => s.data.totalValue);
  const dayPnl = usePortfolioStore((s) => s.data.dayPnl);
  const dayPnlPct = usePortfolioStore((s) => s.data.dayPnlPct);
  const currency = usePortfolioStore((s) => s.data.baseCurrency);
  const session = usePortfolioStore((s) => s.data.session);
  const updatedAt = usePortfolioStore((s) => s.data.updatedAt);
  const status = usePortfolioStore((s) => s.status);
  const error = usePortfolioStore((s) => s.error);

  const history = useValueHistory(totalValue);
  const baseline = totalValue - dayPnl;

  const renderItem = useCallback(
    ({item}: {item: string}) => (
      <PositionRow symbol={item} baseCurrency={currency} flash={!scrolling} />
    ),
    [currency, scrolling],
  );

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <FlatList
        data={order}
        keyExtractor={(symbol) => symbol}
        renderItem={renderItem}
        contentContainerStyle={styles.content}
        onScrollBeginDrag={() => setScrolling(true)}
        onMomentumScrollEnd={() => setScrolling(false)}
        onScrollEndDrag={() => setScrolling(false)}
        ListHeaderComponent={
          <View style={styles.header}>
            <StatusBar status={status} session={session} updatedAt={updatedAt} />

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <Text style={styles.label}>Toplam portföy</Text>
            <ValueTicker
              value={totalValue}
              direction={dayPnl >= 0 ? 'up' : 'down'}
              flash={!scrolling}
              render={(v) => formatMoney(v, currency)}
              style={styles.total}
            />
            <Text style={[styles.change, {color: pnlColor(dayPnl)}]}>
              {formatSignedMoney(dayPnl, currency)} · {formatPercent(dayPnlPct)} bugün
            </Text>

            <Sparkline
              values={history}
              baseline={baseline}
              width={width - spacing.lg * 2}
              height={64}
            />

            <Text style={styles.sectionTitle}>Varlıklar</Text>
          </View>
        }
        ListEmptyComponent={
          <Text style={styles.empty}>
            {status === 'live' ? 'Portföyünüz boş.' : 'Canlı veriye bağlanılıyor…'}
          </Text>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {flex: 1, backgroundColor: colors.background},
  content: {padding: spacing.lg, paddingBottom: spacing.xxl},
  header: {gap: spacing.sm, marginBottom: spacing.lg},
  label: {color: colors.textMuted, ...typography.caption, marginTop: spacing.lg},
  total: {...typography.display, color: colors.text, alignSelf: 'flex-start'},
  change: {...typography.body, ...typography.mono},
  sectionTitle: {color: colors.textMuted, ...typography.caption, marginTop: spacing.lg},
  empty: {color: colors.textFaint, textAlign: 'center', marginTop: spacing.xxl},
  error: {color: colors.down, ...typography.caption},
});
