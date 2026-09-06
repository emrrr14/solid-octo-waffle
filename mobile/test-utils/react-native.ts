/**
 * Minimal React Native stub for Jest.
 *
 * The unit suite covers logic that must not depend on a device: token
 * lifecycle, frame folding, the socket state machine. Pulling in the real RN
 * runtime (or `react-native/jest/setup`) to test them would trade a 3-second
 * suite for a 30-second one and add a native surface to something that has
 * none. Component tests, when they come, want the real preset in a second
 * Jest project.
 */

export const NativeModules: Record<string, unknown> = {};

export const Platform = {
  OS: 'ios' as const,
  select: <T,>(spec: {ios?: T; android?: T; default?: T}): T | undefined =>
    spec.ios ?? spec.default,
};

export const AppState = {
  currentState: 'active' as const,
  addEventListener: () => ({remove: () => undefined}),
};
