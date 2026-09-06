import {Platform} from 'react-native';

/**
 * Endpoints.
 *
 * The iOS **simulator** shares the Mac's loopback, so `localhost` works; a
 * physical device does not, and needs the Mac's LAN address here.  That single
 * difference is the most common "it works on my machine" in RN development, so
 * it is spelled out rather than hidden in a .env file.
 *
 * Production must be `https`/`wss`: App Transport Security blocks cleartext,
 * and the ATS exception in Info.plist is scoped to localhost for exactly this
 * reason.
 */
const DEV_HOST = Platform.select({ios: 'localhost', default: '10.0.2.2'});

export const config = {
  apiBaseUrl: __DEV__ ? `http://${DEV_HOST}:8000` : 'https://api.roboadvisor.example',
  wsUrl: __DEV__ ? `ws://${DEV_HOST}:8000` : 'wss://api.roboadvisor.example',
  portfolioId: 'demo-500try',
  /** Face ID prompt before any order leaves the device. */
  confirmReason: 'Portföy dağılımını onaylamak için kimliğinizi doğrulayın',
} as const;
