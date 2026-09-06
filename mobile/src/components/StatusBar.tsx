import {StyleSheet, Text, View} from 'react-native';

import type {ConnectionStatus, Session} from '../types';
import {formatClock} from '../format';
import {colors, radius, spacing, typography} from '../theme';

/**
 * Connection and session state, always visible.
 *
 * A live-data app must never let the user wonder whether the number in front of
 * them is current.  Three facts, one line: are we connected, is the market
 * open, when did the last frame land.
 */
const SESSION_LABEL: Record<Session, string> = {
  open: 'Piyasa açık',
  pre: 'Açılış öncesi',
  closed: 'Piyasa kapalı',
};

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  idle: 'Bağlanıyor',
  connecting: 'Bağlanıyor',
  live: 'Canlı',
  reconnecting: 'Yeniden bağlanıyor',
  offline: 'Bağlantı yok',
};

const STATUS_COLOR: Record<ConnectionStatus, string> = {
  idle: colors.textMuted,
  connecting: colors.warning,
  live: colors.up,
  reconnecting: colors.warning,
  offline: colors.down,
};

interface Props {
  status: ConnectionStatus;
  session: Session;
  updatedAt: string | null;
}

export function StatusBar({status, session, updatedAt}: Props) {
  return (
    <View style={styles.bar}>
      <View style={styles.group}>
        <View style={[styles.dot, {backgroundColor: STATUS_COLOR[status]}]} />
        <Text style={styles.label}>{STATUS_LABEL[status]}</Text>
      </View>
      <Text style={styles.separator}>·</Text>
      <Text style={styles.label}>{SESSION_LABEL[session]}</Text>
      <View style={styles.spacer} />
      <Text style={[styles.label, styles.clock]}>{formatClock(updatedAt)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    backgroundColor: colors.surfaceRaised,
    borderRadius: radius.sm,
  },
  group: {flexDirection: 'row', alignItems: 'center', gap: spacing.xs},
  dot: {width: 8, height: 8, borderRadius: 4},
  label: {color: colors.textMuted, ...typography.caption},
  clock: {...typography.mono},
  separator: {color: colors.textFaint},
  spacer: {flex: 1},
});
