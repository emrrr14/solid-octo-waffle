# Robo Advisor — iOS app

React Native + TypeScript client for the robo-advisor backend: live portfolio
valuation over websockets, the macro-triggered allocation proposal, and the
Face ID confirmation that releases orders.

## Run it

```bash
npm install
./scripts/bootstrap-ios.sh    # macOS: generates ios/RoboAdvisor.xcworkspace + pods

npm run mock                  # terminal 2 — fake backend (no API keys needed)
npm start                     # terminal 3 — metro
npm run ios                   # terminal 4 — simulator
```

The mock server speaks the same wire contract as the Python backend, so the app
runs end-to-end with no market-data subscription, Redis or database. Point
`src/config.ts` at the real backend when you have one.

```bash
npm test        # 43 tests: session lifecycle, socket state machine, frame folding, formatting
npm run typecheck
```

Sign in with the mock backend's demo account:
`demo@roboadvisor.example` / `demo-password-123`.

## Layout

```
src/
  api/socket.ts        websocket state machine: backoff+jitter, watchdog, 4401 reauth
  api/client.ts        REST with hard timeouts, one refresh-and-retry, idempotent confirm
  api/auth.ts          login / refresh / logout
  auth/session.ts      the device's token owner: single-flight refresh, Keychain storage
  state/
    portfolioReducer.ts  pure snapshot/delta fold — the tested core
    portfolioStore.ts    zustand store with per-row selector subscriptions
  hooks/
    usePortfolioStream.ts  socket lifecycle bound to iOS app lifecycle
    useValueHistory.ts     ring buffer behind the sparkline
  components/          ValueTicker (native-driver flash), PositionRow, Sparkline,
                       WeightBar, StatusBar
  screens/             SignIn, Portfolio (live), Allocation (proposal + Face ID),
                       Macro (why), Account (security state + sign out)
  native/SecureVault.ts  typed face of the Swift module, with a loud fallback
ios/RoboAdvisor/       SecureVault.swift + .m — Keychain and Face ID
server/mock-server.js  development backend
```

## The five decisions that matter

**Server-paced frames, client-paced rendering.** The backend sends one snapshot
then deltas at 1 Hz. The reducer folds them; a delta that arrives before a
snapshot is dropped rather than rendered, because a delta applied to an empty
book invents a portfolio.

**Per-row store subscriptions.** `FlatList` is fed the *symbol array*, and each
row pulls its own slice from zustand. A NVDA tick re-renders the NVDA row.
Passing a positions map down from the screen would re-render thirty rows a
second — the usual reason live portfolio screens run hot.

**Animations on the UI thread.** The value flash is an opacity animation with
`useNativeDriver: true`, so it holds 60fps while JS is busy folding the next
frame, and it is suppressed while the user drags the list.

**Native code only where JS cannot go.** One Swift module: Keychain storage and
Face ID. Everything else is TypeScript. See [`ios/README.md`](ios/README.md).

**Refresh is single-flight.** The server rotates refresh tokens and treats a
replay as theft — it revokes the whole family. If the websocket and two REST
calls each noticed an expired token and refreshed in parallel, two of them would
present an already-used token and the user would be signed out mid-session. All
three share one in-flight refresh instead.

## Not built yet

Push notifications for rebalance alerts, biometric app-unlock on cold start (Face
ID currently gates order confirmation only), and the intraday history endpoint
the sparkline would use instead of its client-side buffer.
