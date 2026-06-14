# Engineering Brief — Seykota-Style Trend-Following Trading Bot (Capital.com)

**Audience:** Claude Code (implementing engineer)
**Author of brief:** Research + spec prepared for Prajish Vinayak
**Version:** 1.0 — June 2026
**Status:** Specification for build. Nothing here should be treated as financial advice.

---

## 0. How to read this brief

This document specifies *what to build and why*, grounded in Ed Seykota's documented trend-following methodology and adapted to the Capital.com CFD API. It is deliberately prescriptive about logic and risk, and deliberately leaves tunable parameters in a single config layer (Section 11). Where a number is Seykota's own (documented) versus a community reconstruction, this is flagged — do not present reconstructed parameters as gospel.

Build order recommended: data layer → indicator/signal engine → risk/sizing engine → backtester → broker adapter (paper/demo) → orchestration/scheduling → monitoring/guardrails → live.

---

## 1. Objective

Build a **systematic, mechanical trend-following bot** that trades a diversified basket of instruments on **Capital.com** (stocks, ETFs, commodities, and index/"futures-style" CFDs), running **24×5** (continuous through the trading week, respecting each instrument's own market hours). The system must:

1. Generate entry/exit signals from a documented Seykota-style trend model (EMA trend filter + breakout/trailing-stop exits).
2. Size every position by **volatility-scaled, price-action-aware risk** — risk a small, fixed fraction of *current* equity per trade, with the dollar risk converted to position size via the instrument's volatility (ATR) and stop distance.
3. Manage portfolio-level risk ("heat"), de-risk automatically in drawdowns, and optionally pyramid into winners.
4. Be fully backtestable on historical data before any live capital is used.
5. Be safe: demo-first, hard guardrails, kill switch, full audit logging.

**Non-goals:** intraday scalping, high-frequency execution, discretionary overrides, machine-learning prediction. This is a rules-based position/swing system.

---

## 2. The Seykota methodology being encoded (research basis)

Ed Seykota is a pioneer of computerized trend following (profiled in Schwager's *Market Wizards*). There are **no peer-reviewed academic papers** by or about him; the source material is his *Market Wizards* interview, his co-authored 1993 article *"Determining Optimal Risk"* (Seykota & Druz), his website seykota.com, the "Whipsaw Song," and the trend-following lineage he influenced (the Turtles descend from the same school). The risk-management layer below is genuinely his and well-sourced; specific entry/exit *parameters* are robust community reconstructions and are flagged as such.

**Documented principles to encode:**

- **Trade with the trend, always be positioned in its direction.** Seykota: *"I turn bullish at the instant my buy stop is hit and stay bullish until my sell stop is hit."* Decision priority (his words): *"(1) the long-term trend, (2) the current chart pattern, (3) a good spot to buy or sell."*
- **Cut losses.** His mantra: *"The elements of good trading are (1) cutting losses, (2) cutting losses, and (3) cutting losses."* Stops are set **at entry** at a level where *"the chart sours,"* then trailed only in the favorable direction — never loosened.
- **Ride winners.** Exit *only* when a trailing stop is hit. No fixed profit targets. *"The trend is your friend except at the end where it bends."*
- **Keep bets small.** Seykota: *"Risk less than 1% of your speculative account on a trade"* (practical ceiling cited up to ~5% allowing for poor fills). Size off **current equity** so the system compounds up and de-risks in drawdowns.
- **Volatility-normalized sizing & portfolio "heat."** From *Determining Optimal Risk*: total portfolio heat = sum of per-trade risk; *"Setting the heat level is far and away more important than fiddling with trade timing parameters."* Risk a similar amount per market regardless of instrument by normalizing to volatility.
- **Pyramiding:** *"Add smaller and smaller amounts on the way up."*
- **Whipsaws are a cost of the method.** A single large trend pays for many small whipsaw losses — expect a **low win rate (~30–45%) with a high payoff ratio**. Do not optimize for win rate; do not abandon the system during drawdowns.

**The 5 rules (verbatim):** 1. Cut losses. 2. Ride winners. 3. Keep bets small. 4. Follow the rules without question. 5. Know when to break the rules.

These principles are the acceptance criteria for the strategy logic: if a proposed implementation violates "cut losses / ride winners / small bets sized to volatility / size off current equity," it is wrong.

---

## 3. Scope, instruments & the CFD reality

**Critical context:** On Capital.com, *everything is a CFD* (contract for difference) — including "stocks," "ETFs," and "commodities/futures." You are not buying shares or exchange futures; you are trading leveraged CFDs on the underlying. This drives several design decisions:

- **Position size** is expressed as CFD **`size`** (units/contracts) per the instrument's spec, not as "number of shares." Each instrument has a minimum deal size, size step, and its own currency.
- **Leverage / margin:** positions consume margin; the bot must track free margin and never exceed it. Risk is controlled by **stop distance**, not by notional.
- **Costs:** spread (bid/ask), overnight financing (swap) on positions held overnight, and possibly currency conversion. Overnight financing matters for a position/swing system — model it.
- **24×5 nuance:** Trading hours differ per instrument. Index and commodity CFDs trade nearly 24×5; single-stock and ETF CFDs only trade during their exchange sessions. Crypto (if enabled) is 24×7 but we constrain to 24×5 per requirement. **The bot must check each instrument's market status (`TRADEABLE`) before acting** and queue/skip signals for closed markets.

**Instrument universe (config-driven):** A diversified basket spanning sectors maximizes the chance of catching the few large trends that carry a trend system. Suggested seed list (epics to be confirmed via `/markets` search at build time): major index CFDs (US500, US100, US30, UK100, DE40), commodities (Gold, Silver, Oil_Crude, Oil_Brent, NatGas, Copper), a handful of liquid large-cap stock CFDs and sector ETFs. Keep correlated instruments grouped for the correlation cap (Section 6.4).

---

## 4. System architecture

Modular, testable components. Suggested Python package layout:

```
seykota_bot/
  config/            # YAML + .env, instrument universe, parameters
  data/              # Capital.com market-data client, historical cache, candle store
  broker/            # Capital.com REST + WebSocket client (auth, orders, positions)
  indicators/        # EMA, ATR/N, Donchian channels, swing detection
  strategy/          # signal engine (entries, exits, trend filter)
  risk/              # position sizing, portfolio heat, drawdown scaling, pyramiding
  engine/            # orchestration loop, scheduler, market-hours, state machine
  backtest/          # historical simulator + metrics
  store/             # persistent state (positions, equity curve, trade log) - SQLite
  monitor/           # logging, alerts (email/Telegram), health checks
  cli.py             # run modes: backtest | paper | live | report
  tests/
```

**Two run cadences** (configurable; default = end-of-day to stay faithful to Seykota's once-nightly signal generation, with an intraday option):
- **EOD mode (default, recommended):** once per day after each market's close, compute signals on completed daily candles, place/adjust orders for the next session. Faithful to Seykota ("I get my price data after the close each day").
- **Intraday mode (optional):** poll on a fixed interval (e.g. every HOUR or HOUR_4 candle close) for instruments that trade ~24h, to better exploit 24×5. Use the same signal logic on the chosen timeframe. **Never act on a forming (incomplete) candle** — only on closed bars (avoids look-ahead/repaint).

---

## 5. Data layer

**Source:** Capital.com REST `GET /prices/{epic}` for historical candles; WebSocket (`OHLCMarketData` / quote subscriptions) for live updates. Supported resolutions: `MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_4, DAY, WEEK`. WebSocket supports **max 40 instruments** per connection.

Requirements:
- Fetch and cache enough history per instrument to warm up the longest indicator (e.g. ≥ 200 daily bars for an EMA(140) + ATR(100)). Store candles in local SQLite/Parquet; backfill on startup, then maintain incrementally.
- Normalize all candles to a common schema: `epic, timestamp(UTC), open, high, low, close, volume, resolution`. Use **bid or mid** consistently (decide and document; mid is typical for signals, but execution uses live bid/ask).
- Handle gaps, partial candles, and the **incomplete-last-candle** rule (drop or flag it).
- A single `MarketDataProvider` interface so the backtester and live engine share identical indicator inputs. **The backtest and live signal code must be the same code path** — no divergence.

---

## 6. Strategy / signal engine

All parameters live in config (Section 11). Defaults below are robust round numbers; Seykota explicitly warned against over-optimization — favor uniform parameters across instruments over per-instrument curve-fitting.

### 6.1 Trend filter (regime)
Long trades only allowed when the trend is up; short only when down.
- **Default:** `EMA(fast=80) > EMA(slow=140)` ⇒ uptrend (long-only); `EMA(80) < EMA(140)` ⇒ downtrend (short-only). (80/140 is the commonly cited "Seykota-style" filter — reconstruction, not confirmed by Seykota.)
- Rule of thumb to retain: **slow EMA length ≥ 3× fast EMA length** to suppress whipsaw. Keep this as a validation constraint on any chosen pair.
- Alternative simpler filter (config switch): single-line `close > EMA(100)` ⇒ uptrend.

### 6.2 Entry trigger
Within an allowed trend direction, enter on a momentum confirmation. Two interchangeable engines (config switch):
- **(A) Donchian breakout (default):** go long when `close` makes a new **N-day high** (default N=50); short on new N-day low. This is the classic Seykota/Donchian-lineage breakout.
- **(B) EMA crossover:** enter long when `EMA(fast)` crosses above `EMA(slow)` (e.g. 50/100), short on the reverse. Use when you want a symmetric always-in style.

Entries are **stop/confirmation-based on closed bars only**, executed on the next bar (EOD: next session open or close; intraday: next candle). No entry against the trend filter.

### 6.3 Exit / stops (the "cut losses, ride winners" core)
Every position has, from the moment it's opened:
1. **Initial protective stop** — a hard stop at a chart-invalidation level. Default = **2 × N** from entry (N = ATR, Section 7.1): long stop = `entry − 2N`, short stop = `entry + 2N`. Optionally anchor to the most recent swing low/high if tighter (price-action stop, Section 8.3).
2. **Trailing stop (ride winners)** — once in profit, trail with a volatility stop that only ratchets favorably:
   - **Chandelier exit (default):** long stop = `HighestHigh(22) − 3 × ATR(22)`; short stop = `LowestLow(22) + 3 × ATR(22)`. Ratchet: `stop = max(prev_stop, new_calc)` for longs.
   - **Seykota-style wide trail (option):** exit when price closes beyond `4 × ATR(100)` from the trade's running extreme — looser, designed to ride very long trends (reconstruction).
   - No fixed profit target. Exit *only* when a stop is hit (or trend filter flips, optional).

Place the protective stop **on the broker** (server-side stop order on the position) so risk is enforced even if the bot disconnects. The trailing logic can be bot-managed (recompute and `PUT` updated stop) and/or use Capital.com's native trailing-stop / guaranteed-stop where available.

### 6.4 Portfolio constraints
- **Max units per instrument:** 4 (Turtle-style cap, including pyramids).
- **Max units across correlated instruments:** 6 (group correlated epics in config).
- **Max total open risk (portfolio heat):** configurable cap, default **≤ 12–20%** of equity (Section 7.3). Reject new entries that would breach it.
- **Correlation awareness:** Seykota — *"watch correlations so you don't have on many trades that are essentially the same."* At minimum, group by sector in config; optionally compute a rolling correlation matrix and cap exposure per cluster.

---

## 7. Risk management & sizing (the heart of the system)

This is the part the user most cares about ("strategy analysis, risk management, and scalable risk based on price action"). It must be implemented exactly and be the single source of truth for every order's size.

### 7.1 Volatility unit N (ATR)
- `N = ATR(period)` with Wilder smoothing (RMA). Defaults: ATR(20) for sizing/stops; ATR(100) available for the wide Seykota trail.
- True Range = max(`High−Low`, `|High−PrevClose|`, `|Low−PrevClose|`).

### 7.2 Per-trade risk and position size
Risk a **fixed fraction of current equity** per trade. Default `risk_pct = 0.01` (1%, Seykota's documented figure); hard cap configurable ≤ 0.05.

Convert risk dollars to CFD size via the **stop distance** (not 1N alone, to be explicit and correct):

```
risk_amount   = equity * risk_pct
stop_distance = entry_price - stop_price            # in price points (abs value)
value_per_point_per_unit = contract_value_per_point # from instrument spec (account ccy)
size = risk_amount / (stop_distance * value_per_point_per_unit)
size = round_down_to_step(size, min_deal_size, size_step)
```

- If `stop_distance = k * N` (e.g. 2N), then `size = (equity * risk_pct) / (k * N * value_per_point)`. This is the volatility-normalized sizing Seykota/Turtles use — a given % move in equity corresponds to the same multiple of N across every instrument, regardless of price level or volatility.
- **Always size off *current* equity** (mark-to-market account balance), so wins compound position size and losses shrink it automatically.
- After sizing, verify **margin available** ≥ required margin; if not, reduce size or skip. Never exceed free margin.
- If `size < min_deal_size`, **skip the trade** (don't round up — that breaks the risk cap).

### 7.3 Portfolio heat
- `position_risk_i = size_i * stop_distance_i * value_per_point_i` (current open risk to its stop, in account ccy).
- `portfolio_heat = Σ position_risk_i / equity`.
- Before any new entry: `projected_heat ≤ heat_cap` (default 0.15; tune 0.10–0.20). Reject if breached. Seykota's research shows return rises then falls as heat grows (drawdown ∝ heat²) — run **well below theoretical optimum** for emotional/financial survivability.

### 7.4 Drawdown de-risking (automatic)
Reduce the equity used for sizing as drawdown deepens (Turtle rule, consistent with Seykota's "systematically keep reducing risk during equity drawdowns"):
- For every **10% drawdown** from peak equity, reduce the **notional sizing equity by 20%**; restore as equity recovers. This shrinks bets during losing streaks for "a gentle financial and emotional touchdown."
- Optional harder rule: pause new entries if drawdown exceeds a configurable max (e.g. 25%).

### 7.5 Pyramiding (optional, off by default)
- Add one unit each time price moves **+0.5N** in favor, up to the 4-unit cap per instrument.
- Each add gets its own 2N stop; **trail all units' stops up to the most recent add** so the whole stack carries a unified tight stop (staircase). Each add is sized by the same risk formula (smaller increments as Seykota describes — risk_pct can decay per add).

---

## 8. Scalable risk based on price action (explicit spec)

The user specifically wants **risk that scales with price action**, not a static fixed lot. The system achieves this through several layered mechanisms — implement all; expose weights/toggles in config:

**8.1 Volatility scaling (primary).** Because size = risk_amount / (stop_distance × value_per_point) and stop_distance is an ATR multiple, **position size automatically shrinks when volatility (N) rises and grows when it falls.** Calm, low-ATR markets get larger size; wild, high-ATR markets get smaller size — at constant dollar risk. This is the core "price-action-scaled" behavior.

**8.2 Equity scaling.** Sizing off current equity means risk dollars rise after wins and fall after losses — and the Section 7.4 drawdown rule accelerates the downside reduction. Risk scales with the equity curve, itself a product of price action.

**8.3 Structure-aware stops.** Stop placement can use **recent swing highs/lows** (price-action structure) instead of, or capped by, the raw ATR multiple: long stop = `min(entry − 2N, last_swing_low − buffer)`. Tighter structure ⇒ smaller stop_distance ⇒ (for fixed risk_amount) *larger* size on high-conviction tight setups; looser structure ⇒ smaller size. Detect swings via fractal/pivot logic (configurable lookback).

**8.4 Trend-strength scaling (optional).** Scale `risk_pct` within a band by a trend-strength score (e.g. ADX, or EMA-spread normalized by ATR, or distance of price above the slow EMA in N units). Stronger, cleaner trends ⇒ risk toward the top of the band; weak/choppy ⇒ toward the bottom or no trade. Clamp to `[risk_min, risk_max]` and never exceed the hard cap.

**8.5 Streak / heat governor (optional).** Reduce `risk_pct` after consecutive losers and/or as portfolio heat approaches its cap; this is a smoothing layer on top of 7.3/7.4. Must never *increase* risk beyond the configured max.

**Precedence:** hard caps (per-trade max, heat cap, margin, drawdown pause) always win over any scaling-up logic. Scaling can only move risk *within* the configured safe band.

---

## 9. Capital.com integration spec

**Environments (use Demo first):**
- Demo base URL: `https://demo-api-capital.backend-capital.com`
- Live base URL: `https://api-capital.backend-capital.com`
- WebSocket: `wss://api-streaming-capital.backend-capital.com/connect`
(Confirm exact hosts in the live docs at build time — see Sources.)

**Authentication & session:**
- Generate an API key in the Capital.com account (Settings → API). Custom password optional/encrypted flow available.
- `POST /session` with header `X-CAP-API-KEY` and body `{ identifier (email), password }`. Response headers return **`CST`** and **`X-SECURITY-TOKEN`** — send both on every subsequent request.
- **Session expires after 10 minutes of inactivity.** Implement keepalive (periodic `GET /session` or `/ping`) and transparent re-auth on `401`.
- Encrypted-password option: `GET /session/encryptionKey` then send the encrypted password — implement if storing/transmitting credentials more securely.

**Key REST endpoints (confirm names/shapes against live Swagger):**

| Purpose | Endpoint |
|---|---|
| Create session / auth | `POST /session` |
| Keepalive / account switch | `GET /session`, `PUT /session` |
| Account(s) & balance | `GET /accounts` |
| Search/browse markets | `GET /markets?searchTerm=`, `GET /marketnavigation` |
| Instrument details (min size, step, hours, margin) | `GET /markets/{epic}` |
| Historical candles | `GET /prices/{epic}?resolution=&max=&from=&to=` |
| Open position | `POST /positions` |
| Amend position (stop/limit/trailing) | `PUT /positions/{dealId}` |
| Close position | `DELETE /positions/{dealId}` |
| List open positions | `GET /positions` |
| Working (pending) orders | `POST/GET/PUT/DELETE /workingorders` |
| Confirm order result | `GET /confirms/{dealReference}` |

**Order placement notes:**
- Orders accept `epic, direction (BUY/SELL), size, stopLevel/stopDistance, limitLevel/limitDistance`, and trailing-stop / guaranteed-stop flags where supported. Place the **protective stop with the order** so it lives server-side.
- After `POST /positions` you get a `dealReference`; poll `GET /confirms/{dealReference}` to get `dealId` and fill status. Persist the `dealId` for later amend/close.
- Use `GET /markets/{epic}` to read **min deal size, size step, market status, and margin factor** before sizing — never hardcode.

**Rate limits (respect strictly, with backoff):**
- `POST /session`: **1 req/sec** per API key.
- General: **max 1 req / 0.1 s** per user; position/order creation also ~1 per 0.1 s.
- Demo `POST /positions` & `POST /workingorders`: **1000 req/hour**.
- WebSocket: **max 40 instruments**, **10-min session** — send `ping` to keep alive; resubscribe on reconnect; WS drops when you switch financial account via `PUT /session`.

**Market data via WebSocket:** subscribe to quotes/OHLC for the active universe (≤40). Use REST `GET /prices` for backfill and as fallback. Drive signals off **closed candles** only.

---

## 10. Execution engine, scheduling & 24×5

- **Main loop / scheduler:** a resilient long-running service (e.g. `asyncio` loop or APScheduler). In EOD mode, schedule each instrument's signal evaluation just after its session close (per-instrument timezone). In intraday mode, evaluate on each chosen-resolution candle close.
- **Market-hours gating:** before any action on an epic, check `marketStatus == TRADEABLE` (from `/markets/{epic}` or the snapshot). Skip/queue if closed. 24×5 means the loop is always alive across the week but only acts on instruments currently open.
- **State machine per instrument:** `FLAT → PENDING_ENTRY → IN_POSITION (managing stop) → EXITING → FLAT`. Persist state to SQLite so a restart recovers cleanly.
- **Reconciliation on startup & each cycle:** fetch `GET /positions` and reconcile against local state; the broker is the source of truth. Adopt/repair any drift (e.g. a stop that didn't get placed).
- **Idempotency:** never double-submit. Use `dealReference`/`confirms` and local locks.
- **Weekend handling:** flatten or hold per config; account for weekend gap risk and overnight financing.

---

## 11. Configuration (single source of tunables)

Provide `config.yaml` + `.env` (secrets). Everything tunable lives here; **no magic numbers in code.**

```yaml
account:
  environment: demo            # demo | live
  base_url_demo:  https://demo-api-capital.backend-capital.com
  base_url_live:  https://api-capital.backend-capital.com
  # API key, identifier, password come from .env, never committed

mode:
  run_mode: paper              # backtest | paper | live
  cadence: eod                 # eod | intraday
  intraday_resolution: HOUR_4  # used when cadence=intraday

strategy:
  trend_filter: ema_pair       # ema_pair | single_ema
  ema_fast: 80
  ema_slow: 140                # constraint: slow >= 3 * fast
  entry: donchian_breakout     # donchian_breakout | ema_cross
  donchian_n: 50
  exit: chandelier             # chandelier | seykota_wide
  chandelier_period: 22
  chandelier_mult: 3.0
  seykota_atr_period: 100
  seykota_atr_mult: 4.0
  exit_on_trend_flip: false
  allow_short: true

risk:
  atr_period: 20
  risk_pct: 0.01               # per-trade, fraction of equity (Seykota: <1%)
  risk_pct_max: 0.02           # hard cap for any scaling
  initial_stop_atr_mult: 2.0   # 2N initial stop
  heat_cap: 0.15               # max total open risk / equity
  max_units_per_instrument: 4
  max_units_correlated: 6
  drawdown_derisk: true        # -20% sizing equity per -10% DD
  max_drawdown_pause: 0.25     # pause new entries beyond this DD
  pyramiding:
    enabled: false
    add_every_n: 0.5           # add unit per +0.5N
    max_adds: 3

price_action_scaling:
  volatility_scaling: true     # inherent in ATR sizing
  structure_stops: true        # cap stop at recent swing
  swing_lookback: 10
  trend_strength_scaling: false
  trend_strength_indicator: adx
  streak_governor: true

universe:
  - {epic: GOLD,  group: metals}
  - {epic: SILVER, group: metals}
  - {epic: OIL_CRUDE, group: energy}
  - {epic: NATURALGAS, group: energy}
  - {epic: US500, group: equity_index}
  - {epic: US100, group: equity_index}
  # ... confirm exact epics via GET /markets at build time

execution:
  slippage_model_ticks: 1
  respect_market_hours: true
  weekend_policy: hold         # hold | flatten

monitoring:
  alerts: telegram             # telegram | email | none
  log_level: INFO
```

---

## 12. Backtesting & validation (required before live)

Build a backtester that shares the **exact** indicator/signal/risk code with the live engine (no reimplementation).

- **Data:** Capital.com `GET /prices` history (or an imported CSV provider). Document the bid/mid choice. Account for spread + overnight financing in P&L.
- **No look-ahead:** signal on closed bar *t*, fill on bar *t+1* (open or close, configurable). Apply slippage (default 1 tick/side) and spread; charge financing for overnight holds.
- **Walk-forward / out-of-sample:** don't curve-fit. Test the same parameters across the whole basket and across multiple regimes. Seykota's warning about over-optimization is a hard requirement — prefer robustness to peak backtest return.
- **Metrics to output** (formulas in the appendix): CAGR, max drawdown, Sharpe, Sortino, MAR/Calmar, win rate, profit factor, avg win/loss (payoff ratio), expectancy (in $ and R-multiples), exposure, longest drawdown duration, per-instrument contribution. Expect a **low win rate, high payoff ratio, fat-tailed winners** — that's the signature of a working trend system, not a bug.
- **Reports/dashboard:** equity curve, drawdown underwater plot, per-trade table, monthly returns heatmap, rolling Sharpe. (A static HTML report via Plotly, or a small Streamlit/Dash app, is fine — implementer's choice.)

---

## 13. Safety, guardrails & monitoring

- **Demo-first:** ship and run on the Demo environment until metrics and behavior are verified. Live is a config flip plus an explicit human confirmation step.
- **Hard guardrails (enforced in code, not just config):** per-trade risk cap, portfolio heat cap, margin check, max-drawdown pause, max open positions, and a **global kill switch** (flatten-all + halt) reachable via CLI/alert command.
- **Daily loss limit:** halt new entries if realized+unrealized daily loss exceeds a configured % of equity.
- **Connectivity resilience:** auto re-auth on session expiry/401, WS reconnect + resubscribe, REST retry with exponential backoff respecting rate limits, and **broker-side stops** so risk persists if the bot dies.
- **Reconciliation & alerts:** on every cycle reconcile local vs broker positions; alert on drift, on order rejects, on stop-not-placed, on disconnects, and on fills.
- **Audit log:** persist every signal, order, fill, stop change, and equity snapshot (SQLite + structured logs) for later analysis and for debugging "why did it trade."
- **No financial advice / human-in-the-loop for live:** the bot executes its own rules; a human owns the decision to run it live, fund it, and stop it. **Do not have the bot move real money without the user's explicit go-live action.**

---

## 14. Tech stack & deliverables

**Suggested stack:** Python 3.11+, `httpx`/`requests` (REST), `websockets` (WS), `pandas`/`numpy` (indicators), `pandas-ta` or hand-rolled indicators, `APScheduler`/`asyncio` (scheduling), `SQLite`/`SQLAlchemy` (state), `pydantic` (config validation), `pytest` (tests), `plotly`/`streamlit` (reporting). `python-dotenv` for secrets.

**Deliverables Claude Code should produce:**
1. The package in Section 4 with working **backtest**, **paper (demo)**, and **live** modes behind a CLI.
2. A reusable, well-typed **Capital.com client** (auth, session keepalive, market data, orders, positions) with rate-limit handling.
3. The **indicator, signal, and risk engines** as pure, unit-tested functions.
4. A **backtest report/dashboard**.
5. `config.yaml` + `.env.example`, a `README` (setup, API-key creation, run instructions), and **tests** covering sizing math, heat cap, stop logic, and no-look-ahead.
6. Clear **TODO markers** wherever a Capital.com field/epic/host must be confirmed against the live API.

**Acceptance criteria:** (a) sizing math provably risks ≤ `risk_pct` of equity to the stop on every trade; (b) portfolio heat never exceeds cap; (c) no look-ahead in backtest (verified by a test); (d) broker-side protective stop present on every live/paper position; (e) graceful session re-auth and WS reconnect; (f) demo run places, manages, trails, and closes a position end-to-end; (g) kill switch flattens and halts.

---

## 15. Open decisions to confirm at build time

- Exact Capital.com epics for each instrument (resolve via `GET /markets` search) and their min size / step / margin factor / hours.
- Bid vs mid for signal candles; fill assumption (next open vs next close).
- EOD vs intraday as the primary cadence (default EOD).
- Final parameter set after walk-forward (start from the defaults in Section 11; do not over-fit).
- Whether to enable shorts on stock/ETF CFDs (borrow/financing implications).

---

## Appendix A — Metric formulas

Let `r_t` = periodic returns, `P` = periods/year (252 daily).

- **CAGR** = `(End/Start)^(1/years) − 1`
- **Max Drawdown** = `min_t(equity_t / running_peak_t − 1)`
- **Sharpe** = `mean(r) / std(r) × √P`
- **Sortino** = `mean(r) / downside_dev × √P`, where `downside_dev = √(mean(min(r,0)²))`
- **MAR / Calmar** = `CAGR / |MaxDrawdown|`
- **Win rate** = `wins / trades`
- **Profit factor** = `gross_profit / |gross_loss|`
- **Payoff ratio** = `avg_win / avg_loss`
- **Expectancy ($)** = `win% × avg_win − loss% × avg_loss`
- **Expectancy (R)** = same, with wins/losses expressed in multiples of initial risk (R)

## Appendix B — Sizing worked example (CFD)

Equity = $50,000; `risk_pct` = 1% ⇒ risk_amount = $500. Instrument N (ATR20) = 4.0 points; initial stop = 2N = 8.0 points; value_per_point_per_unit = $10.
`size = 500 / (8.0 × 10) = 6.25 units` → round down to step. Position risk to stop = `6.25 × 8.0 × 10 = $500` = 1% of equity. ✔
If volatility doubles (N = 8 ⇒ stop = 16 points), `size = 500 / (16 × 10) = 3.125 units` — **size halves automatically as volatility rises**, holding dollar risk constant (price-action scaling in action).

---

## Sources

Seykota methodology:
- [DayTrading.com — Ed Seykota Strategy & Philosophy (Market Wizards quotes)](https://www.daytrading.com/ed-seykota)
- [Seykota & Druz, "Determining Optimal Risk," Stocks & Commodities V.11:3 (PDF)](https://www.trendfollowing.com/whitepaper/DETERMI.PDF)
- [TrendFollowing.com — Ed Seykota / Whipsaw Song](https://www.trendfollowing.com/ed_seykota/)
- [New Trader U — Ed Seykota's 6 Rules from the Whipsaw Song](https://www.newtraderu.com/2011/12/11/ed-seykotas-6-rules-from-the-whipsaw-song/)
- [QuantifiedStrategies — Ed Seykota's Trading Strategies](https://www.quantifiedstrategies.com/ed-seykota-trading-strategies/)

Trend-following / Turtle implementation (sizing, stops, pyramiding, metrics):
- [Original Turtle Trading Rules (PDF)](https://www.tradingwithrayner.com/wp-content/uploads/2014/11/OriginalTurtleRules.pdf)
- [QuantifiedStrategies — Position Sizing in a Turtle System](https://www.quantifiedstrategies.com/position-sizing-in-a-turtle-trading-system/)
- [StockCharts — Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit)
- [LuxAlgo — Backtesting metrics](https://www.luxalgo.com/blog/top-7-metrics-for-backtesting-results/)
- [QuantPedia — Continuous futures contracts for backtesting](https://quantpedia.com/continuous-futures-contracts-methodology-for-backtesting/)

Capital.com API:
- [Capital.com Public API portal (Swagger)](https://open-api.capital.com/)
- [API documentation guide](https://capital.com/en-int/trading-platforms/api-development-guide)
- [Which kind of APIs do you have? (REST + WebSocket, 40-instrument cap)](https://help.capital.com/hc/en-us/articles/6630762294418-Which-kind-of-APIs-do-you-have)
- [Getting CST & X-SECURITY-TOKEN](https://help.capital.com/hc/en-us/articles/5595698273298-How-can-I-get-CST-and-X-SECURITY-TOKEN-parameters)
- [API limitations / rate limits](https://help.capital.com/hc/en-us/articles/6630830103058-Do-you-have-any-limitations-on-your-API)
- [Capital.com Postman collection (GitHub)](https://github.com/capital-com-sv/capital-api-postman)

*Reconstructed parameters (80/140 EMA, 50-day breakout, 4×ATR) are community conventions consistent with Seykota's documented philosophy, not parameters he published. Treat them as defaults to be validated by backtest, not as Seykota's actual system.*
