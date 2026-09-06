import {useEffect, useState} from 'react';
import {ActivityIndicator, StatusBar, StyleSheet, View} from 'react-native';
import {NavigationContainer, type Theme} from '@react-navigation/native';
import {createBottomTabNavigator} from '@react-navigation/bottom-tabs';
import {SafeAreaProvider} from 'react-native-safe-area-context';

import {SecureVault} from './native/SecureVault';
import {AllocationScreen} from './screens/AllocationScreen';
import {MacroScreen} from './screens/MacroScreen';
import {PortfolioScreen} from './screens/PortfolioScreen';
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
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Session bootstrap.  In production this is the sign-in flow writing a
    // real bearer token into the Keychain; in development we seed one so the
    // socket has something to present to the mock server.
    (async () => {
      const existing = await SecureVault.readSession();
      if (!existing && __DEV__) {
        await SecureVault.saveSession('dev-token');
      }
      setReady(true);
    })();
  }, []);

  if (!ready) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar barStyle="light-content" backgroundColor={colors.background} />
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
        </Tab.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  splash: {flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center'},
});
