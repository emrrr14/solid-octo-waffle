import {useEffect, useState} from 'react';
import {ActivityIndicator, StatusBar, StyleSheet, View} from 'react-native';
import {NavigationContainer, type Theme} from '@react-navigation/native';
import {createBottomTabNavigator} from '@react-navigation/bottom-tabs';
import {SafeAreaProvider} from 'react-native-safe-area-context';

import {AccountScreen} from './screens/AccountScreen';
import {AllocationScreen} from './screens/AllocationScreen';
import {MacroScreen} from './screens/MacroScreen';
import {PortfolioScreen} from './screens/PortfolioScreen';
import {SignInScreen} from './screens/SignInScreen';
import {session} from './session';
import type {AuthState} from './auth/session';
import {colors} from './theme';

const Tab = createBottomTabNavigator();

const navigationTheme: Theme = {
  dark: true,
  colors: {
    primary: colors.accent,
    background: colors.background,
    card: colors.surface,
    text: colors.text,
    border: colors.border,
    notification: colors.accent,
  },
  fonts: {
    regular: {fontFamily: 'System', fontWeight: '400'},
    medium: {fontFamily: 'System', fontWeight: '500'},
    bold: {fontFamily: 'System', fontWeight: '700'},
    heavy: {fontFamily: 'System', fontWeight: '800'},
  },
};

export default function App() {
  const [auth, setAuth] = useState<AuthState>('unknown');

  useEffect(() => {
    // Cold start: a refresh token in the Keychain is exchanged for an access
    // token before anything renders, so a returning user never sees the
    // sign-in screen flash past.
    const unsubscribe = session.subscribe(setAuth);
    void session.restore();
    return unsubscribe;
  }, []);

  if (auth === 'unknown') {
    return (
      <View style={styles.splash}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" backgroundColor={colors.background} />
      {auth === 'signed-out' ? (
        <SignInScreen onSignedIn={() => setAuth(session.getState())} />
      ) : (
        <NavigationContainer theme={navigationTheme}>
          <Tab.Navigator
            screenOptions={{
              headerShown: false,
              tabBarActiveTintColor: colors.accent,
              tabBarInactiveTintColor: colors.textFaint,
              tabBarStyle: {backgroundColor: colors.surface, borderTopColor: colors.border},
            }}>
            <Tab.Screen name="Portföy" component={PortfolioScreen} />
            <Tab.Screen name="Dağılım" component={AllocationScreen} />
            <Tab.Screen name="Makro" component={MacroScreen} />
            <Tab.Screen name="Hesap">
              {() => <AccountScreen onSignedOut={() => setAuth('signed-out')} />}
            </Tab.Screen>
          </Tab.Navigator>
        </NavigationContainer>
      )}
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  splash: {flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center'},
});
