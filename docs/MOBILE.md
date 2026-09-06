# Mobile architecture

The iOS client lives in [`mobile/`](../mobile). This document covers the choices
behind it; the how-to-run is in that folder's README.

## Why React Native rather than Swift

Honest framing: for *this* app, native Swift would produce a marginally better
result and cost roughly twice the engineering. The deciding factors were:

* **The hard parts are not the UI.** Streaming valuation, the factor model and
  the LP all run server-side. The client renders numbers, gates a confirmation,
  and manages a socket. That is exactly the workload React Native handles well.
* **Two platforms, one team.** Android costs marginally more from here, not
  another codebase.
* **The one thing RN is bad at is absent.** RN struggles with sustained
  high-frequency UI work driven from JS. We deliberately do not have that: the
  server coalesces to 1 Hz, so the JS thread folds one frame per second and the
  only 60fps work (the flash) runs on the native driver.

Where RN would have been the wrong call: if the product needed an order book,
tick-level charting, or sub-100ms interaction against a streaming feed, the JS
bridge would be in the way and Swift/SwiftUI would win.

## What is written in what

| Layer | Language | Why |
|---|---|---|
| UI, navigation, state, formatting | TypeScript | the whole product surface |
| Websocket + REST clients | TypeScript | plain I/O; nothing platform-specific |
| Session token storage | **Swift** (Keychain) | `AsyncStorage` is plaintext; only Security.framework gives hardware-backed, backup-excluded storage |
| Order confirmation | **Swift** (LocalAuthentication) | Face ID has no JS equivalent |
| Everything else | — | native modules are a permanent build cost; two justified ones is the whole list |

## Data flow

```
backend ws  ──1 Hz──▶  PortfolioSocket  ──frames──▶  portfolioReducer (pure)
                            │                              │
                    watchdog│backoff                       ▼
                            │                        zustand store
                            │                    ┌─────────┴─────────┐
                     AppState (iOS)         selector          selector
                   background→close         totals            per symbol
                   active→reconnect            │                 │
                                          header/sparkline    PositionRow
```

REST is a separate path: the allocation proposal, the macro feed and the
confirmation are request/response with an audit trail, not a stream.

## The failure modes this client is built around

| Reality | Handling |
|---|---|
| iOS suspends a backgrounded app; sockets die silently | close on `background`/`inactive`, reconnect on `active`, server replies with a fresh snapshot |
| Wifi→cellular leaves a half-open socket that never fires `onclose` | 45 s inbound-frame watchdog against the server's 15 s heartbeat |
| 50k phones leaving a tunnel together | exponential backoff with full jitter |
| An expired token retried forever | close codes 4401/4403/4404 are terminal and surface to the UI |
| A delta arriving before any snapshot | dropped; a delta for an unknown symbol sets `needsResync` |
| Double-tapping "confirm" on a bad connection | `Idempotency-Key` = decision id, so a replay returns the original outcome |
| Licensed prices and private quantities | valuation is server-side; the client never multiplies quantity by price |

## Performance notes

* **Tabular figures everywhere.** Without `fontVariant: ['tabular-nums']`, every
  digit change shifts the layout and a 1 Hz ticker appears to vibrate.
* **Flash suppressed while scrolling.** Animation under a moving finger competes
  with the scroll for the UI thread and reads as jank.
* **The sparkline is thinned.** 1 Hz for a session is 28,800 points; the ring
  buffer keeps 120 samples at every 5th frame, which is the same trade-off a
  server-side downsample makes, minus the round trip.

## Accessibility and locale

Gains are green *and signed*; losses red *and signed*. Roughly 8% of men have a
red/green colour-vision deficiency, and a number that means something only by hue
is unreadable to them. Currency formatting goes through `Intl` with an explicit
Turkish fallback, so a build where Hermes lacks ICU degrades to `1.234,50 ₺`
rather than to whatever the engine's default happens to be.

## Left to build

Sign-in and token refresh, APNs push for rebalance alerts (APNs is not a data
channel — push a notification, fetch on open), an intraday history endpoint to
replace the client-side sparkline buffer, and Android verification.
