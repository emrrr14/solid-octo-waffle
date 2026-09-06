/**
 * TypeScript face of the Swift Keychain/Face ID module (`ios/RoboAdvisor/`).
 *
 * On a simulator without the native module linked (or during a Jest run) this
 * degrades to an in-memory store *and says so loudly*.  A silent fallback that
 * looks like it works is how a build ships with tokens in plain memory.
 */

import {NativeModules, Platform} from 'react-native';

export type BiometryType = 'face' | 'touch' | 'none';

interface SecureVaultNative {
  setToken(token: string, account: string): Promise<boolean>;
  getToken(account: string): Promise<string | null>;
  deleteToken(account: string): Promise<boolean>;
  biometryType(): Promise<BiometryType>;
  authenticate(reason: string): Promise<boolean>;
}

const native = NativeModules.SecureVault as SecureVaultNative | undefined;

let warned = false;
const memory = new Map<string, string>();

function fallback(): SecureVaultNative {
  if (!warned) {
    warned = true;
    console.warn(
      `[SecureVault] native module unavailable on ${Platform.OS} - ` +
        'using in-memory storage. Tokens are NOT persisted or protected. ' +
        'Never ship a build in this state.',
    );
  }
  return {
    async setToken(token, account) {
      memory.set(account, token);
      return true;
    },
    async getToken(account) {
      return memory.get(account) ?? null;
    },
    async deleteToken(account) {
      memory.delete(account);
      return true;
    },
    async biometryType() {
      return 'none';
    },
    async authenticate() {
      return true;
    },
  };
}

const vault: SecureVaultNative = native ?? fallback();

const ACCOUNT = 'session';

export const SecureVault = {
  saveSession: (token: string) => vault.setToken(token, ACCOUNT),
  readSession: () => vault.getToken(ACCOUNT),
  clearSession: () => vault.deleteToken(ACCOUNT),
  biometryType: () => vault.biometryType(),
  /** Gate a money-moving action. Resolves false when the user cancels. */
  confirmWithBiometrics: (reason: string) => vault.authenticate(reason),
  isNative: native !== undefined,
};
