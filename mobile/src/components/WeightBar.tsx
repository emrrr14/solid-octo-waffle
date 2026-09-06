import {StyleSheet, Text, View} from 'react-native';

import {formatWeight} from '../format';
import {colors, radius, spacing, typography} from '../theme';

/**
 * Current vs target weight for one asset, as two stacked bars.
 *
 * Showing both is the point: a single "target" bar tells the user what the
 * optimiser wants, not what it is asking them to *do*.  The gap between the
 * bars is the trade.
 */
interface Props {
  label: string;
  assetClass: string;
  current: number;
  target: number;
  order: number;
  currencySymbol: string;
}

export function WeightBar({label, assetClass, current, target, order, currencySymbol}: Props) {
  const delta = target - current;
  const deltaColor = delta > 0 ? colors.up : delta < 0 ? colors.down : colors.textMuted;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View>
          <Text style={styles.label}>{label}</Text>
          <Text style={styles.class}>{assetClass}</Text>
        </View>
        <View style={styles.headerRight}>
          <Text style={styles.weights}>
            {formatWeight(current)} → {formatWeight(target)}
          </Text>
          <Text style={[styles.order, {color: deltaColor}]}>
            {order >= 0 ? '+' : '−'}
            {Math.abs(order).toFixed(2)} {currencySymbol}
          </Text>
        </View>
      </View>

      <View style={styles.track}>
        <View style={[styles.fill, styles.currentFill, {width: `${current * 100}%`}]} />
      </View>
      <View style={styles.track}>
        <View style={[styles.fill, styles.targetFill, {width: `${target * 100}%`}]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.lg,
    marginBottom: spacing.sm,
    gap: spacing.xs,
  },
  header: {flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.sm},
  headerRight: {alignItems: 'flex-end'},
  label: {color: colors.text, ...typography.body},
  class: {color: colors.textFaint, ...typography.caption, marginTop: 2},
  weights: {color: colors.textMuted, ...typography.caption, ...typography.mono},
  order: {...typography.caption, ...typography.mono, marginTop: 2},
  track: {height: 6, backgroundColor: colors.surfaceRaised, borderRadius: 3, overflow: 'hidden'},
  fill: {height: '100%', borderRadius: 3},
  currentFill: {backgroundColor: colors.textFaint},
  targetFill: {backgroundColor: colors.accent},
});
