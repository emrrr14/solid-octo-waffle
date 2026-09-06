import {useEffect, useState} from 'react';
import {Alert, Pressable, StyleSheet, Text, View} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';

import {SecureVault, type BiometryType} from '../native/SecureVault';
import {session} from '../session';
import {colors, radius, spacing, typography} from '../theme';

const BIOMETRY_LABEL: Record<BiometryType, string> = {
  face: 'Face ID etkin',
  touch: 'Touch ID etkin',
  none: 'Biyometrik doğrulama kapalı',
};

/**
 * Account and security state.
 *
 * The "storage" row is not decoration: it says out loud whether the Keychain
 * module is actually linked. A build where it silently fell back to memory
 * looks identical from every other screen, and that is exactly the build you
 * must never ship.
 */
export function AccountScreen({onSignedOut}: {onSignedOut: () => void}) {
  const [biometry, setBiometry] = useState<BiometryType>('none');

  useEffect(() => {
    void SecureVault.biometryType().then(setBiometry);
  }, []);

  const signOut = () => {
    Alert.alert('Çıkış yap', 'Bu cihazdaki oturumunuz kapatılacak.', [
      {text: 'Vazgeç', style: 'cancel'},
      {
        text: 'Çıkış yap',
        style: 'destructive',
        onPress: async () => {
          await session.signOut();
          onSignedOut();
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <View style={styles.content}>
        <Text style={styles.title}>Hesap</Text>

        <View style={styles.card}>
          <Row label="Biyometri" value={BIOMETRY_LABEL[biometry]} />
          <Row
            label="Oturum saklama"
            value={SecureVault.isNative ? 'iOS Keychain' : 'Bellek (güvenli değil)'}
            warn={!SecureVault.isNative}
          />
        </View>

        <Pressable style={({pressed}) => [styles.signOut, pressed && styles.pressed]} onPress={signOut}>
          <Text style={styles.signOutText}>Çıkış yap</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

function Row({label, value, warn = false}: {label: string; value: string; warn?: boolean}) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={[styles.rowValue, warn && styles.rowWarn]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: {flex: 1, backgroundColor: colors.background},
  content: {padding: spacing.lg, gap: spacing.lg},
  title: {color: colors.text, ...typography.title},
  card: {backgroundColor: colors.surface, borderRadius: radius.md, padding: spacing.lg, gap: spacing.md},
  row: {flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center'},
  rowLabel: {color: colors.textMuted, ...typography.caption},
  rowValue: {color: colors.text, ...typography.caption},
  rowWarn: {color: colors.warning},
  signOut: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: spacing.lg,
    alignItems: 'center',
  },
  pressed: {opacity: 0.7},
  signOutText: {color: colors.down, ...typography.body},
});
