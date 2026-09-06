/**
 * Development stand-in for the Python backend.
 *
 * Speaks the exact wire contract of `backend/app/api/ws.py` and the REST
 * endpoints the app calls, so the whole iOS client can be built and demoed with
 * no market-data keys, no Redis and no database.  Run it with `npm run mock`.
 *
 * It also reproduces the two behaviours that break naive clients, so they get
 * exercised in development rather than in production:
 *   - snapshot first, deltas after (never a delta into an empty book);
 *   - a heartbeat when nothing moved, so the client's watchdog is fed.
 */
const http = require('http');
const {WebSocketServer} = require('ws');

const PORT = 8000;
const BASE_CURRENCY = 'TRY';
const USDTRY = 34.2;

const HOLDINGS = [
  {symbol: 'NVDA', quantity: 12, price: 118.4, currency: 'USD', drift: 0.0012},
  {symbol: 'SPY', quantity: 4, price: 571.2, currency: 'USD', drift: 0.0004},
  {symbol: 'THYAO.IS', quantity: 140, price: 292.5, currency: 'TRY', drift: 0.0009},
  {symbol: 'TEFAS:AFA', quantity: 900, price: 1.284, currency: 'TRY', drift: 0.0},
  {symbol: 'TEFAS:GLD', quantity: 1200, price: 0.912, currency: 'TRY', drift: 0.0},
];

const state = HOLDINGS.map((h) => ({...h, last: h.price, prevClose: h.price}));

const fx = (currency) => (currency === 'USD' ? USDTRY : 1);
const value = (h) => h.quantity * h.last * fx(h.currency);
const prevValue = (h) => h.quantity * h.prevClose * fx(h.currency);

function step() {
  for (const h of state) {
    if (h.drift === 0) continue; // funds price once a day, not per second
    const shock = (Math.random() - 0.5) * 0.0025 + h.drift / 60;
    h.last = Number((h.last * (1 + shock)).toFixed(4));
  }
}

function totals() {
  const total = state.reduce((sum, h) => sum + value(h), 0);
  const prev = state.reduce((sum, h) => sum + prevValue(h), 0);
  const dayPnl = total - prev;
  return {
    total_value: Number(total.toFixed(2)),
    day_pnl: Number(dayPnl.toFixed(2)),
    day_pnl_pct: Number(((dayPnl / prev) * 100).toFixed(4)),
  };
}

const snapshot = () => ({
  type: 'snapshot',
  ts: new Date().toISOString(),
  session: 'open',
  base_currency: BASE_CURRENCY,
  ...totals(),
  positions: state.map((h) => ({
    symbol: h.symbol,
    quantity: h.quantity,
    last_price: h.last,
    price_currency: h.currency,
    fx_rate: fx(h.currency),
    market_value_base: Number(value(h).toFixed(2)),
    prev_close_base: Number(prevValue(h).toFixed(2)),
    stale: h.drift === 0,
  })),
});

const delta = (moved) => ({
  type: 'delta',
  ts: new Date().toISOString(),
  session: 'open',
  ...totals(),
  positions: moved.map((h) => ({
    symbol: h.symbol,
    last_price: h.last,
    market_value: Number(value(h).toFixed(2)),
    day_pnl: Number((value(h) - prevValue(h)).toFixed(2)),
    stale: false,
  })),
});

// ---------------------------------------------------------------- REST

const ALLOCATION = {
  portfolio_id: 'demo-500try',
  decision_id: 'decision-mock-1',
  decided_at: new Date().toISOString(),
  trigger_reason: 'rate_cut -25bp on 2026-09-05 (-25bp vs expectation)',
  notional: 500,
  base_currency: 'TRY',
  status: 'Optimization terminated successfully. (HiGHS Status 7: Optimal)',
  applied: true,
  note: '',
  expected_return_annual: 0.331,
  risk_mad: 0.00194,
  turnover: 0.6,
  binding_constraints: ['turnover_budget'],
  rows: [
    {symbol: 'AFA', name: 'BIST Hisse Fonu', asset_class: 'equity_tr', current_weight: 0.25, target_weight: 0.061, target_amount: 30.65, order_amount: -94.35},
    {symbol: 'IPB', name: 'Yabancı Hisse Fonu', asset_class: 'equity_global', current_weight: 0.2, target_weight: 0.089, target_amount: 44.35, order_amount: -55.65},
    {symbol: 'TTE', name: 'Devlet Tahvili Fonu', asset_class: 'bond_tr', current_weight: 0.2, target_weight: 0.25, target_amount: 125, order_amount: 25},
    {symbol: 'GLD', name: 'Altın Fonu', asset_class: 'gold', current_weight: 0.1, target_weight: 0.1, target_amount: 50, order_amount: 0},
    {symbol: 'MMK', name: 'Para Piyasası Fonu', asset_class: 'money_market', current_weight: 0.1, target_weight: 0.35, target_amount: 175, order_amount: 125},
    {symbol: 'EUB', name: 'Eurobond Fonu', asset_class: 'fx', current_weight: 0.15, target_weight: 0.15, target_amount: 75, order_amount: 0},
  ],
};

const MACRO_EVENTS = [
  {id: '1', series: 'DFEDTARU', kind: 'rate_cut', observed_on: '2026-09-05', previous_value: 5.5, new_value: 5.25, change_bps: -25, surprise_bps: -25, triggered_rebalance: true, note: ''},
  {id: '2', series: 'DFEDTARU', kind: 'rate_cut', observed_on: '2026-07-30', previous_value: 5.75, new_value: 5.5, change_bps: -25, surprise_bps: 0, triggered_rebalance: false, note: 'Piyasada fiyatlanmıştı, işlem yapılmadı'},
  {id: '3', series: 'TR_CPI_YOY', kind: 'inflation_print', observed_on: '2026-07-03', previous_value: 38.1, new_value: 35.4, change_bps: -270, surprise_bps: -80, triggered_rebalance: true, note: ''},
];

// ---------------------------------------------------------------- auth
//
// Mirrors the real contract closely enough to exercise the client: rotating
// refresh tokens, a 401 on anything unknown, and bearer auth on every read.

const DEMO = {email: 'demo@roboadvisor.example', password: 'demo-password-123'};
const accessTokens = new Set();
const refreshTokens = new Set();
let issued = 0;

function issuePair() {
  issued += 1;
  const access = `mock-access-${issued}`;
  const refresh = `mock-refresh-${issued}`;
  accessTokens.add(access);
  refreshTokens.add(refresh);
  return {access_token: access, refresh_token: refresh, token_type: 'bearer', expires_in: 900};
}

function isAuthorised(req) {
  const header = req.headers.authorization ?? '';
  return header.startsWith('Bearer ') && accessTokens.has(header.slice(7));
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', () => {
      try {
        resolve(JSON.parse(raw || '{}'));
      } catch {
        resolve({});
      }
    });
  });
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  const url = req.url ?? '';

  if (url === '/api/auth/login') {
    const body = await readBody(req);
    if (body.email !== DEMO.email || body.password !== DEMO.password) {
      res.statusCode = 401;
      res.end(JSON.stringify({detail: 'Invalid credentials'}));
      return;
    }
    res.end(JSON.stringify(issuePair()));
    return;
  }

  if (url === '/api/auth/refresh') {
    const body = await readBody(req);
    if (!refreshTokens.delete(body.refresh_token)) {
      // Unknown or already-rotated: the real server would also revoke the
      // family here, which from the client's side looks exactly like this.
      res.statusCode = 401;
      res.end(JSON.stringify({detail: 'Invalid credentials'}));
      return;
    }
    res.end(JSON.stringify(issuePair()));
    return;
  }

  if (url === '/api/auth/logout') {
    const body = await readBody(req);
    refreshTokens.delete(body.refresh_token);
    res.statusCode = 204;
    res.end();
    return;
  }

  if (!isAuthorised(req)) {
    res.statusCode = 401;
    res.end(JSON.stringify({detail: 'Invalid credentials'}));
    return;
  }

  if (url.includes('/allocation')) {
    res.end(JSON.stringify(ALLOCATION));
  } else if (url.startsWith('/api/macro/events')) {
    res.end(JSON.stringify(MACRO_EVENTS));
  } else if (url.includes('/rebalance/confirm')) {
    res.end(JSON.stringify({accepted: true}));
  } else {
    res.statusCode = 404;
    res.end(JSON.stringify({detail: 'not found'}));
  }
});

// ---------------------------------------------------------------- WebSocket

const wss = new WebSocketServer({
  server,
  // Echo the client's "bearer" subprotocol; without it the client closes the
  // connection itself, which is a confusing failure to debug from the app side.
  handleProtocols: (protocols) => (protocols.has('bearer') ? 'bearer' : false),
});

wss.on('connection', (socket, request) => {
  const offered = (request.headers['sec-websocket-protocol'] ?? '').split(',').map((p) => p.trim());
  const token = offered[0] === 'bearer' ? offered[1] : undefined;
  if (!token || !accessTokens.has(token)) {
    console.log('[ws] rejected unauthenticated handshake');
    socket.close(4401, 'unauthenticated');
    return;
  }

  console.log(`[ws] client connected: ${request.url}`);
  socket.send(JSON.stringify(snapshot()));

  let sinceFrame = 0;
  const timer = setInterval(() => {
    const before = state.map((h) => h.last);
    step();
    const moved = state.filter((h, i) => h.last !== before[i]);

    if (moved.length > 0) {
      socket.send(JSON.stringify(delta(moved)));
      sinceFrame = 0;
    } else if (++sinceFrame >= 15) {
      socket.send(JSON.stringify({type: 'heartbeat', ts: new Date().toISOString(), session: 'open'}));
      sinceFrame = 0;
    }
  }, 1000);

  socket.on('close', () => {
    clearInterval(timer);
    console.log('[ws] client disconnected');
  });
});

server.listen(PORT, () => {
  console.log(`mock backend on http://localhost:${PORT}  (ws://localhost:${PORT}/ws/portfolio/:id)`);
});
