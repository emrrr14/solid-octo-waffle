import {useCallback, useEffect, useState} from 'react';
import {FlatList, RefreshControl, StyleSheet, Text, View} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';

import {ApiClient} from '../api/client';
import {config} from '../config';
import {formatBps} from '../format';
import {session} from '../session';
import type {MacroEventView} from '../types';
import {colors, radius, spacing, typography} from '../theme';

const api = new ApiClient({baseUrl: config.apiBaseUrl, session});

const KIND_LABEL: Record<MacroEventView['kind'], string> = {
  rate_cut: 'Faiz indirimi',
  rate_hike: 'Faiz artırımı',
  rate_hold: 'Faiz sabit',
  inflation_print: 'Enflasyon verisi',
};

/**
 * The macro feed - why the portfolio moved.
 *
 * The surprise column is the one that matters and the one users won't have
 * seen elsewhere: a fully-priced cut leaves the portfolio alone, and this
 * screen is where that becomes visible instead of looking like a bug.
 */
export function MacroScreen() {
  const [events, setEvents] = useState<MacroEventView[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setEvents(await api.getMacroEvents());
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <FlatList
        data={events}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={load} tintColor={colors.textMuted} />
        }
        ListHeaderComponent={<Text style={styles.title}>Makro olaylar</Text>}
        ListEmptyComponent={
          <Text style={styles.empty}>{loading ? 'Yükleniyor…' : 'Kayıt yok'}</Text>
        }
        renderItem={({item}) => (
          <View style={styles.card}>
            <View style={styles.row}>
              <Text style={styles.kind}>{KIND_LABEL[item.kind]}</Text>
              <Text style={styles.date}>{item.observed_on}</Text>
            </View>

            <Text style={styles.change}>
              {item.previous_value.toFixed(2)}% → {item.new_value.toFixed(2)}%{'  '}
              <Text style={styles.bps}>({formatBps(item.change_bps)})</Text>
            </Text>

            <View style={styles.row}>
              <Text style={styles.surpriseLabel}>Beklentiye göre sürpriz</Text>
              <Text
                style={[
                  styles.surprise,
                  {color: item.surprise_bps === 0 ? colors.textMuted : colors.warning},
                ]}>
                {formatBps(item.surprise_bps)}
              </Text>
            </View>

            <Text
              style={[
                styles.outcome,
                {color: item.triggered_rebalance ? colors.accent : colors.textFaint},
              ]}>
              {item.triggered_rebalance ? 'Dağılım güncellendi' : item.note}
            </Text>
          </View>
        )}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {flex: 1, backgroundColor: colors.background},
  content: {padding: spacing.lg, paddingBottom: spacing.xxl},
  title: {color: colors.textMuted, ...typography.caption, marginBottom: spacing.md},
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.lg,
    marginBottom: spacing.sm,
    gap: spacing.xs,
  },
  row: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center'},
  kind: {color: colors.text, ...typography.body},
  date: {color: colors.textFaint, ...typography.caption, ...typography.mono},
  change: {color: colors.textMuted, ...typography.caption, ...typography.mono},
  bps: {color: colors.text},
  surpriseLabel: {color: colors.textFaint, ...typography.caption},
  surprise: {...typography.caption, ...typography.mono},
  outcome: {...typography.caption, marginTop: spacing.xs},
  empty: {color: colors.textFaint, textAlign: 'center', marginTop: spacing.xxl},
});
