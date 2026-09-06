import {useCallback, useEffect, useState} from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';

import {ApiClient} from '../api/client';
import {WeightBar} from '../components/WeightBar';
import {config} from '../config';
import {formatMoney, formatPercent, formatWeight} from '../format';
import {SecureVault} from '../native/SecureVault';
import {session} from '../session';
import type {AllocationView} from '../types';
import {colors, radius, spacing, typography} from '../theme';

const api = new ApiClient({baseUrl: config.apiBaseUrl, session});

/**
 * The proposed allocation, and the button that accepts it.
 *
 * This screen is the regulatory boundary of the product: automated portfolio
 * management for retail investors in Türkiye is SPK-licensed activity, so the
 * app *proposes* and the user *confirms*.  That shapes the UI - the optimiser's
 * reasoning (what triggered it, which constraint bound, what it costs in
 * turnover) is shown, not hidden behind a "trust us" spinner.
 *
 * Confirmation is gated by Face ID and carries an idempotency key, so a double
 * tap or a retry after a timeout cannot place two sets of orders.
 */
export function AllocationScreen() {
  const [data, setData] = useState<AllocationView | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setData(await api.getAllocation(config.portfolioId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const confirm = useCallback(async () => {
    if (!data) return;
    const approved = await SecureVault.confirmWithBiometrics(config.confirmReason);
    if (!approved) return; // user cancelled - not an error, no toast, no noise

    setSubmitting(true);
    try {
      await api.confirmRebalance(config.portfolioId, data.decision_id);
      Alert.alert('Emirler iletildi', 'Dağılım güncellemesi işleme alındı.');
      await load();
    } catch (e) {
      Alert.alert('Emirler iletilemedi', (e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [data, load]);

  if (loading) {
    return (
      <SafeAreaView style={styles.centered}>
        <ActivityIndicator color={colors.accent} />
      </SafeAreaView>
    );
  }

  if (error || !data) {
    return (
      <SafeAreaView style={styles.centered}>
        <Text style={styles.error}>{error ?? 'Dağılım bulunamadı'}</Text>
        <Pressable onPress={load} style={styles.retry}>
          <Text style={styles.retryText}>Tekrar dene</Text>
        </Pressable>
      </SafeAreaView>
    );
  }

  const symbol = data.base_currency === 'TRY' ? '₺' : data.base_currency;
  const hasOrders = data.rows.some((row) => row.order_amount !== 0);

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.textMuted} />
        }>
        <Text style={styles.title}>Önerilen dağılım</Text>
        <Text style={styles.notional}>{formatMoney(data.notional, data.base_currency)}</Text>

        <View style={styles.triggerCard}>
          <Text style={styles.triggerLabel}>Tetikleyici</Text>
          <Text style={styles.triggerReason}>{data.trigger_reason}</Text>
        </View>

        <View style={styles.metrics}>
          <Metric label="Beklenen getiri" value={formatPercent(data.expected_return_annual * 100)} />
          <Metric label="Risk (MAD)" value={(data.risk_mad * 100).toFixed(3) + '%'} />
          <Metric label="Devir" value={formatWeight(data.turnover)} />
        </View>

        {data.binding_constraints.length > 0 ? (
          <View style={styles.constraints}>
            <Text style={styles.constraintsLabel}>Bağlayıcı kısıtlar</Text>
            <Text style={styles.constraintsBody}>{data.binding_constraints.join(', ')}</Text>
            <Text style={styles.constraintsHint}>
              Optimizasyon bu sınırlara dayandı; daha fazla ilerlemek isterdi.
            </Text>
          </View>
        ) : null}

        {data.rows.map((row) => (
          <WeightBar
            key={row.symbol}
            label={`${row.symbol} · ${row.name}`}
            assetClass={row.asset_class}
            current={row.current_weight}
            target={row.target_weight}
            order={row.order_amount}
            currencySymbol={symbol}
          />
        ))}

        {hasOrders ? (
          <Pressable
            style={({pressed}) => [styles.cta, pressed && styles.ctaPressed]}
            disabled={submitting}
            onPress={confirm}>
            <Text style={styles.ctaText}>
              {submitting ? 'İletiliyor…' : 'Face ID ile onayla'}
            </Text>
          </Pressable>
        ) : (
          <Text style={styles.noOrders}>{data.note || 'Değişiklik gerekmiyor.'}</Text>
        )}

        <Text style={styles.disclaimer}>
          Bu bir yatırım tavsiyesi değildir. Emirler yalnızca sizin onayınızla iletilir.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Metric({label, value}: {label: string; value: string}) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: {flex: 1, backgroundColor: colors.background},
  centered: {flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center', gap: spacing.md},
  content: {padding: spacing.lg, paddingBottom: spacing.xxl},
  title: {color: colors.textMuted, ...typography.caption},
  notional: {color: colors.text, ...typography.display, marginBottom: spacing.lg},
  triggerCard: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: radius.md,
    padding: spacing.lg,
    marginBottom: spacing.md,
  },
  triggerLabel: {color: colors.textFaint, ...typography.caption},
  triggerReason: {color: colors.text, ...typography.body, marginTop: spacing.xs},
  metrics: {flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.lg},
  metric: {flex: 1, backgroundColor: colors.surface, borderRadius: radius.md, padding: spacing.md},
  metricLabel: {color: colors.textFaint, fontSize: 11},
  metricValue: {color: colors.text, ...typography.body, ...typography.mono, marginTop: 2},
  constraints: {
    borderLeftWidth: 2,
    borderLeftColor: colors.warning,
    paddingLeft: spacing.md,
    marginBottom: spacing.lg,
  },
  constraintsLabel: {color: colors.warning, ...typography.caption},
  constraintsBody: {color: colors.text, ...typography.caption, ...typography.mono, marginTop: 2},
  constraintsHint: {color: colors.textFaint, fontSize: 11, marginTop: 2},
  cta: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: spacing.lg,
    alignItems: 'center',
    marginTop: spacing.md,
  },
  ctaPressed: {opacity: 0.85},
  ctaText: {color: '#fff', ...typography.body},
  noOrders: {color: colors.textMuted, ...typography.caption, textAlign: 'center', marginTop: spacing.md},
  disclaimer: {color: colors.textFaint, fontSize: 11, textAlign: 'center', marginTop: spacing.lg},
  error: {color: colors.down, ...typography.body},
  retry: {paddingHorizontal: spacing.lg, paddingVertical: spacing.sm},
  retryText: {color: colors.accent, ...typography.body},
});
