import {useCallback, useState} from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';

import {session} from '../session';
import {colors, radius, spacing, typography} from '../theme';

/**
 * Sign-in.
 *
 * Deliberately plain, with three things that matter on iOS specifically:
 * `textContentType` so the Keychain and iCloud Keychain offer to fill and save
 * credentials, `autoCapitalize="none"` on the email field (iOS capitalises by
 * default and users blame the app for "wrong password"), and a single generic
 * error message - the server refuses to distinguish an unknown account from a
 * wrong password, and the UI must not undo that.
 */
export function SignInScreen({onSignedIn}: {onSignedIn: () => void}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await session.signIn(email.trim(), password, Platform.OS === 'ios' ? 'iOS' : 'Android');
      onSignedIn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [email, password, onSignedIn]);

  const canSubmit = email.includes('@') && password.length >= 8 && !busy;

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={styles.container}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <Text style={styles.title}>Robo Advisor</Text>
        <Text style={styles.subtitle}>Portföyünüze giriş yapın</Text>

        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="E-posta"
          placeholderTextColor={colors.textFaint}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="username"
          returnKeyType="next"
        />
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          placeholder="Şifre"
          placeholderTextColor={colors.textFaint}
          secureTextEntry
          textContentType="password"
          returnKeyType="go"
          onSubmitEditing={canSubmit ? submit : undefined}
        />

        {error ? <Text style={styles.error}>{error}</Text> : null}

        <Pressable
          style={({pressed}) => [styles.cta, !canSubmit && styles.ctaDisabled, pressed && styles.ctaPressed]}
          disabled={!canSubmit}
          onPress={submit}>
          {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.ctaText}>Giriş yap</Text>}
        </Pressable>

        <View style={styles.footer}>
          <Text style={styles.footerText}>
            Oturumunuz cihazın Keychain'inde saklanır ve emirler Face ID ile onaylanır.
          </Text>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {flex: 1, backgroundColor: colors.background},
  container: {flex: 1, justifyContent: 'center', padding: spacing.xl, gap: spacing.md},
  title: {color: colors.text, ...typography.display},
  subtitle: {color: colors.textMuted, ...typography.body, marginBottom: spacing.lg},
  input: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    color: colors.text,
    ...typography.body,
  },
  cta: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingVertical: spacing.lg,
    alignItems: 'center',
    marginTop: spacing.sm,
  },
  ctaDisabled: {opacity: 0.4},
  ctaPressed: {opacity: 0.85},
  ctaText: {color: '#fff', ...typography.body},
  error: {color: colors.down, ...typography.caption},
  footer: {marginTop: spacing.xl},
  footerText: {color: colors.textFaint, fontSize: 11, textAlign: 'center'},
});
