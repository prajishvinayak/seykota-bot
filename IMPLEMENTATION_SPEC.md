# Seykota-Style Trading Bot — Complete Implementation Specification

**For:** Claude Code (build this)
**Prepared for:** Prajish Vinayak
**Version:** 1.0 — June 2026
**Companion docs:** `STRATEGY_RULES.md` (the rulebook), `Seykota_Trading_Bot_Brief.md` (narrative brief)

> This is a build-ready specification. It contains the exact strategy logic, the risk math, reference algorithms (Python), the Capital.com integration details, the module architecture, the config schema, and acceptance tests. Implement it faithfully; tune only the values in `config.yaml`.

---

## 0. Honest framing (keep in the README)

Ed Seykota's legendary "~300,000%" came from compounding roughly **60%/year for ~16+ years** with leverage, while *surviving* drawdowns north of 50%. The durable edge is **risk control and discipline through losing streaks**, not entry cleverness. Capital.com itself discloses that **~78% of retail CFD accounts lose money**. This system is engineered to maximise the chance of being in the other 22% — it is **not** a promise of returns. Mandatory sequence: **backtest on 3 years of real data → paper-trade on Capital.com demo → only then consider live**, as an explicit human decision.

Expect the signature of a real trend follower: **low win rate (~35–45%)**, **high payoff ratio**, equity that grinds sideways/down between a few large trend captures. Do not judge it on individual trades or short windows.

---

## 1. What the system does

A mechanical, rules-based trend-following bot that:
1. Trades a diversified basket of **Capital.com CFDs** — equity indices, commodities/metals/energy, and selected liquid stock/ETF CFDs — **24×5**, respecting each instrument's own market hours.
2. Generates entries/exits from an **EMA trend filter + Donchian breakout + ATR trailing-stop** model (Seykota lineage).
3. Sizes every position by **volatility-scaled risk** — a small fixed % of *current* equity, converted to position size via the stop distance, so size auto-shrinks when volatility rises.
4. Caps **portfolio heat**, **de-risks in drawdowns**, and optionally **pyramids** winners.
5. Is fully **backtestable** on 3 years of real data with realistic costs, **before** any capital is risked.
6. Produces a **dashboard** and a **detailed end-of-day report** that explains every losing (RED) trade so the user can iterate.

**Non-goals:** intraday scalping, ML prediction, discretionary overrides (except a global kill switch).

---

## 2. Strategy & rules (authoritative summary)

Full rulebook in `STRATEGY_RULES.md`. The mechanical core:

| # | Rule | Default |
|---|------|---------|
| Trend filter | Long only if `EMA(fast) > EMA(slow)`; short only if `<`. Constraint: `slow ≥ 3×fast`. | EMA 80 / 140 |
| Entry | In the gated direction, enter on a new **N-bar Donchian breakout** (close ≥ prior N-bar high / ≤ prior N-bar low). | N = 50 |
| Fill timing | Signal on **closed bar t**, fill at **bar t+1 open**. No look-ahead. | — |
| Initial stop | `2 × N` from entry (`N = ATR(20)`), server-side. May tighten to recent swing. | 2N |
| Exit | **Chandelier trailing stop** only, ratcheting favourably; no profit target. | `HH(22) − 3×ATR(22)` |
| Risk/trade | Fixed % of **current** equity, sized via stop distance. | 1% (cap 2%) |
| Portfolio heat | Reject entries that push total open risk / equity above cap. | 15% |
| Drawdown de-risk | −20% sizing equity per −10% drawdown; pause new entries beyond max DD. | pause @ 25% |
| Pyramiding | Optional: +1 unit per +0.5N, max 4 units, trail combined stop. | off |
| Price-action scaling | Volatility sizing (always) + structure stops + optional trend-strength & streak governors. | see §5.4 |

**The 5 laws:** cut losses · ride winners · keep bets small · follow the rules · know when to break them (= kill switch only).

---

## 3. Research basis (why these rules)

There are **no academic papers** by/about Seykota; sources are his *Market Wizards* interview, his co-authored 1993 article *"Determining Optimal Risk"* (Seykota & Druz), seykota.com, the "Whipsaw Song," and the Turtle lineage that descends from the same school. Documented, sourced principles encoded above:

- *"Cut losses, cut losses, cut losses."* → 2N initial stop, server-side, never loosened.
- *"Ride your winners… let a trailing stop take you out."* → chandelier trail, no targets.
- *"Risk less than 1% of your speculative account on a trade"* → `risk_pct` default 1%.
- Volatility-normalized sizing + **portfolio "heat"**: *"Setting the heat level is far more important than fiddling with trade timing."* → §5.
- *"Add smaller and smaller amounts on the way up."* → pyramiding.
- Whipsaws are an accepted cost — *one good trend pays for them all*; **don't abandon the system in drawdowns.**

The specific *parameters* (80/140 EMA, 50-bar breakout, 4×ATR) are robust community reconstructions consistent with his philosophy — **defaults to validate by backtest, not numbers Seykota published.** Full sources in §13.

---

## 4. Capital.com integration (REST + WebSocket)

Everything on Capital.com is a **CFD** (leveraged), including "stocks/ETFs/commodities." Position size is CFD `size` (units), not shares. Risk is controlled by stop distance; track margin and overnight financing.

**Environments**
- Demo REST base: `https://demo-api-capital.backend-capital.com`
- Live REST base: `https://api-capital.backend-capital.com`
- WebSocket: `wss://api-streaming-capital.backend-capital.com/connect`
- API version prefix: `/api/v1`. (Confirm exact paths against the live Swagger at build time.)

**Auth & session**
1. Create API key in Capital.com → Settings → API (optionally with a custom/encrypted password).
2. `POST /api/v1/session` with header `X-CAP-API-KEY` and body `{identifier: <email>, password: <pw>}`.
3. Response headers contain **`CST`** and **`X-SECURITY-TOKEN`** — send **both** on every subsequent request.
4. Session expires after **10 min inactivity** → implement keepalive (`GET /api/v1/session` / ping) and transparent re-auth on `401`.
5. Encrypted-password flow: `GET /api/v1/session/encryptionKey` then send the RSA-encrypted password. Implement for secure credential handling.

**Key endpoints**

| Purpose | Endpoint |
|---|---|
| Server time / connectivity | `GET /api/v1/time` |
| Create session | `POST /api/v1/session` |
| Keepalive / switch account | `GET /api/v1/session`, `PUT /api/v1/session` |
| Accounts & balance | `GET /api/v1/accounts` |
| Search markets | `GET /api/v1/markets?searchTerm=` |
| Market detail (min size, step, hours, margin, status) | `GET /api/v1/markets/{epic}` |
| Historical candles | `GET /api/v1/prices/{epic}?resolution=&max=&from=&to=` |
| Open position | `POST /api/v1/positions` |
| Amend stop/limit/trailing | `PUT /api/v1/positions/{dealId}` |
| Close position | `DELETE /api/v1/positions/{dealId}` |
| List open positions | `GET /api/v1/positions` |
| Working (pending) orders | `POST/GET/PUT/DELETE /api/v1/workingorders` |
| Confirm a deal result | `GET /api/v1/confirms/{dealReference}` |

- **Price resolutions:** `MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_4, DAY, WEEK`.
- **Order body** (positions): `{epic, direction: BUY|SELL, size, stopLevel|stopDistance, limitLevel|limitDistance, trailingStop, guaranteedStop}`. **Always send the protective stop with the order** so it lives server-side. After `POST`, poll `GET /confirms/{dealReference}` for `dealId` + fill; persist `dealId`.
- Read **min deal size / size step / margin factor / market status** from `GET /markets/{epic}` before sizing — never hardcode.

**Rate limits (respect with backoff)**
- `POST /session`: **1 req/sec** per API key.
- General & order creation: **≤ 1 req / 0.1 s** per user.
- Demo `POST /positions` & `/workingorders`: **1000 req/hour**.
- WebSocket: **max 40 instruments**, **10-min session** → send `ping` to keep alive; resubscribe on reconnect; stream drops when you switch account via `PUT /session`.

**Reference client (implement to this interface):**
```python
class CapitalClient:
    def __init__(self, base_url, api_key, identifier, password): ...
    def login(self) -> None:           # POST /session, store CST + X-SECURITY-TOKEN
    def keepalive(self) -> None:       # GET /session; re-login on 401
    def accounts(self) -> list: ...
    def market(self, epic) -> dict:    # min size, step, margin, status, hours
    def search(self, term) -> list: ...
    def price_history(self, epic, resolution="DAY", start=None, end=None, max=1000) -> list[dict]:
        # returns [{snapshotTime, open, high, low, close, volume}, ...]; page backwards to cover 3y
    def open_position(self, epic, direction, size, stop_distance=None,
                      stop_level=None, trailing=False, guaranteed=False) -> str:  # returns dealId
    def amend_position(self, deal_id, stop_level=None, trailing=None) -> None: ...
    def close_position(self, deal_id) -> None: ...
    def positions(self) -> list[dict]: ...
    def confirm(self, deal_reference) -> dict: ...
# All requests send headers: X-CAP-API-KEY, CST, X-SECURITY-TOKEN.
# Wrap a token-bucket rate limiter (<=1 req/0.1s; <=1 session/s) + exponential backoff on 429/401.
```

---

## 5. Risk & sizing engine (the heart — implement exactly)

### 5.1 Volatility unit N
`N = ATR(period)` with **Wilder smoothing** (EMA, α = 1/period). TR = max(`H−L`, `|H−PrevClose|`, `|L−PrevClose|`).
```python
def atr_wilder(df, period):
    pc = df["close"].shift(1)
    tr = pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()
```

### 5.2 Position size (volatility-scaled)
```python
risk_amount   = sizing_equity * risk_pct                 # default risk_pct = 0.01
stop_distance = abs(entry_price - stop_price)            # = 2N by default (points)
size          = risk_amount / (stop_distance * value_per_point_per_unit)
size          = floor(size / size_step) * size_step      # round DOWN to broker step
if size < min_deal_size:  skip_trade()                   # never round up
```
- `value_per_point_per_unit` = account-ccy P&L for a 1.0 price move on 1 unit (from `GET /markets/{epic}` + contract spec).
- **Size off *current* equity** (mark-to-market) so wins compound and losses de-leverage.
- If `stop_distance = k·N`, then `size = (equity·risk_pct)/(k·N·value_per_point)` → identical N-risk across instruments. **This is the core "price-action scaling": higher ATR ⇒ smaller size at constant dollar risk.**
- Before sending: confirm **free margin ≥ required margin** (`size × price × margin_factor`); else reduce or skip.

**Worked example:** equity \$50,000, risk 1% ⇒ \$500 risk. N=4 pts, 2N stop = 8 pts, value/point = \$10 ⇒ `size = 500/(8×10) = 6.25` units; risk to stop = `6.25×8×10 = \$500` = 1%. If volatility doubles (N=8 ⇒ stop 16 pts) ⇒ `size = 3.125` (halves automatically).

### 5.3 Portfolio heat
```python
position_risk_i = size_i * abs(price_i - stop_i) * value_per_point_i   # account ccy
portfolio_heat  = sum(position_risk_i) / equity
# reject new entry if (current_heat_$ + new_risk_$) / equity > heat_cap   # default 0.15
```

### 5.4 Price-action-scaled risk (layered; all bounded by hard caps)
1. **Volatility scaling** — built into §5.2 (always on).
2. **Structure stops** — tighten the stop to the recent swing low/high when closer: `stop = max(entry−2N, swing_low − 0.1N)` (long). Tighter structure ⇒ smaller stop_distance ⇒ right-sized size.
3. **Trend-strength scaling (optional)** — scale `risk_pct` within `[risk_pct, risk_pct_max]` by trend strength (e.g. `|EMA_fast−EMA_slow|/N`, or ADX). Strong clean trend ⇒ toward max; chop ⇒ toward min.
4. **Streak/heat governor (optional)** — reduce `risk_pct` after consecutive losers or as heat nears cap. **Never increases risk above the hard cap.**

### 5.5 Drawdown de-risking
```python
dd = max(0, (peak_equity - equity)/peak_equity)
factor = max(0.2, 1 - 2 * floor(dd/0.10) * 0.10)   # -20% sizing equity per -10% DD
sizing_equity = equity * factor
if dd >= max_drawdown_pause:  block_new_entries()    # default 0.25
```

### 5.6 Pyramiding (optional)
Add 1 unit per **+0.5N** favourable move, max **4 units/instrument** (and 6/correlated group). Each add gets its own 2N stop; **trail all units' stops up to the latest add** (staircase). Each add sized by §5.2 (optionally decaying risk_pct per add).

---

## 6. Indicators & signals (reference)

```python
def build_indicators(df, cfg):
    out = df.copy()
    out["ema_fast"]   = df.close.ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema_slow"]   = df.close.ewm(span=cfg.ema_slow, adjust=False).mean()
    out["atr"]        = atr_wilder(df, cfg.atr_period)
    out["atr_chan"]   = atr_wilder(df, cfg.chandelier_period)
    out["donchian_hi"]= df.high.rolling(cfg.donchian_n).max().shift(1)   # prior N bars
    out["donchian_lo"]= df.low.rolling(cfg.donchian_n).min().shift(1)
    out["hh_chan"]    = df.high.rolling(cfg.chandelier_period).max()
    out["ll_chan"]    = df.low.rolling(cfg.chandelier_period).min()
    out["swing_lo"]   = df.low.rolling(cfg.swing_lookback).min()
    out["swing_hi"]   = df.high.rolling(cfg.swing_lookback).max()
    out["uptrend"]    = out.ema_fast > out.ema_slow
    return out

# Entry signal on closed bar t (execute at t+1 open):
long_entry  = uptrend  and close >= donchian_hi
short_entry = (not uptrend) and close <= donchian_lo and allow_short

# Trailing stop update each bar (ratchet only):
# long:  stop = max(prev_stop, hh_chan - chandelier_mult*atr_chan)
# short: stop = min(prev_stop, ll_chan + chandelier_mult*atr_chan)
```

---

## 7. Backtest engine (3 years real data)

**Hard requirements**
- **Same code path** for indicators/signals/risk in backtest and live — no reimplementation.
- **Data:** 3 years from Capital.com `/prices` (resolution `DAY` default; `HOUR_4` optional) or yfinance fallback. Document bid vs mid.
- **No look-ahead:** signal on closed bar t → fill at t+1 open. Drop the incomplete last candle.
- **Costs:** apply half-spread per side, slippage (≥1 tick), and **overnight financing** per unit per day held.
- **Portfolio-level loop** (shared equity across instruments) so heat/drawdown rules are global.

**Reference event loop (per global trading day):**
```
for date in calendar:
    fill_pending_entries(at open)            # from prior day's signals
    for each open position:
        accrue financing
        update extreme + ratchet trailing stop
        if stop hit intraday: exit (gap-through -> fill at open), record trade
        elif exit_on_trend_flip and filter flipped: exit at close
    equity = balance + unrealized(close)
    peak = max(peak, equity); dd = (peak-equity)/peak
    if dd < max_drawdown_pause:
        for each flat instrument:
            if breakout signal in trend direction:
                compute 2N (+structure) stop, size (§5.2), check heat/units/margin
                queue entry for next day's open
    record equity_curve point {date, equity, drawdown, open_positions, heat}
liquidate_open_at_last_close()
```

**Metrics to output** (formulas in Appendix A): start/end equity, total return, **CAGR, max drawdown, Sharpe, Sortino, MAR/Calmar**, num trades, **win rate, profit factor, payoff ratio, avg win/loss, expectancy ($ and R)**, exposure, longest DD duration, per-instrument P&L.

**Validation:** walk-forward / out-of-sample; **uniform parameters across the whole basket** (Seykota's warning against over-optimization is a hard requirement). A unit test must assert **no look-ahead** (shifting signals one bar cannot improve results).

---

## 8. Dashboard (display everything)

A self-contained **HTML** file (Chart.js from CDN; embed data as JSON) — opens in a browser, no server. Sections:
- **Metric cards:** total return, CAGR, max DD, Sharpe, Sortino, MAR, trades, win rate, profit factor, payoff, avg win/loss, expectancy, end equity.
- **Equity curve** (line) and **drawdown + portfolio heat** (overlaid %).
- **"Why losing trades went RED"** — counts by reason (see §9).
- **P&L by instrument** table.
- **All trades** table (entry/exit, side, bars held, P&L, exit reason, red reason).
(Optional alternative: a Streamlit/Dash app driven from the same `results.json`.)

---

## 9. End-of-day report (iterate together)

After each session the bot writes a **Markdown (and/or HTML) EOD report** containing: account snapshot (equity, drawdown, open positions, heat), today's opened/closed trades, realised P&L, current open positions with stops & unrealised P&L, and — **for every losing trade — the classified reason**:

| Code | Meaning |
|---|---|
| `WHIPSAW` | Breakout reversed within the noise band in a few bars. Expected cost; one trend pays for many. |
| `TREND_REVERSAL` | Trend filter flipped against the position after entry; exit cut the loss. |
| `VOLATILITY_STOP` | ATR expanded; trailing/initial stop hit on a normal pullback. Healthy risk control. |
| `GAP` | Price gapped through the stop (overnight/weekend) → worse fill. Consider guaranteed stops. |
| `FINANCING` | Small loss driven mostly by spread + overnight financing on a flat trade. |
| `RULE_OK_LOSS` | Textbook small loss the system is supposed to take. No change needed. |

**Classifier logic (reference):**
```python
def classify_loss(trade, indicators):
    if trade.bars_held <= 5:                       return "WHIPSAW"
    if trend_filter_flipped(indicators, trade):    return "TREND_REVERSAL"
    if trade.exit_reason == "STOP":                return "VOLATILITY_STOP"
    return "RULE_OK_LOSS"
# extend with GAP (exit_price beyond stop by > slippage tolerance) and
# FINANCING (|pnl| dominated by accrued financing) checks.
```
Each report ends with **"Iterate together"**: the levers to tune in config (never the core rules mid-stream) — `chandelier_mult`, `donchian_n`, `risk_pct`/`heat_cap`, `ema_fast/ema_slow`. Wire EOD report generation to a **scheduled task** (cron) per market close.

---

## 10. Live/paper trading loop (Capital.com demo first)

```
on schedule (EOD per market close, or HOUR_4 close intraday):
    client.keepalive()
    reconcile: GET /positions  ->  sync local state to broker (broker = source of truth)
    for each open position: recompute trailing stop -> PUT amend if tighter
    refresh candles (REST backfill + WS for live quotes, <=40 instruments)
    for each flat & TRADEABLE instrument:
        if breakout signal in trend direction and risk checks pass:
            size via §5.2; POST /positions WITH server-side stop; confirm dealId; persist
    enforce guardrails (heat cap, daily loss limit, max DD pause, max units)
    write EOD report + refresh dashboard
```
- **State machine per instrument:** `FLAT → PENDING_ENTRY → IN_POSITION → EXITING → FLAT`, persisted to SQLite for clean restart.
- **Idempotency:** never double-submit (use dealReference/confirms + local locks).
- **Resilience:** re-auth on 401, WS reconnect+resubscribe, REST retry w/ backoff respecting limits, **broker-side stops** so risk survives a crash.
- **Demo/live = config flag**, with an explicit human confirmation step for live. **The bot must never move real money without the user's deliberate go-live action.**

---

## 11. Config schema (`config.yaml` + `.env` for secrets)

No magic numbers in code — everything tunable lives here.
```yaml
account:
  environment: demo            # demo | live
  base_url_demo: https://demo-api-capital.backend-capital.com
  base_url_live: https://api-capital.backend-capital.com
  ws_url: wss://api-streaming-capital.backend-capital.com/connect
  # CAP_API_KEY, CAP_IDENTIFIER, CAP_PASSWORD come from .env (never commit)

mode:
  run_mode: backtest           # backtest | paper | live
  cadence: eod                 # eod | intraday
  intraday_resolution: HOUR_4

strategy:
  trend_filter: ema_pair       # ema_pair | single_ema
  ema_fast: 80
  ema_slow: 140                # constraint: slow >= 3*fast
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
  starting_equity: 100000
  atr_period: 20
  risk_pct: 0.01
  risk_pct_max: 0.02
  initial_stop_atr_mult: 2.0
  heat_cap: 0.15
  max_units_per_instrument: 4
  max_units_correlated: 6
  drawdown_derisk: true
  max_drawdown_pause: 0.25
  trend_strength_scaling: false
  streak_governor: true
  pyramiding: { enabled: false, add_every_n: 0.5, max_adds: 3 }

execution:
  slippage_ticks: 1
  respect_market_hours: true
  weekend_policy: hold         # hold | flatten
  daily_loss_limit_pct: 0.06

universe:                      # confirm epics via GET /markets at build time
  - {epic: US500,      group: equity_idx}
  - {epic: US100,      group: equity_idx}
  - {epic: DE40,       group: equity_idx}
  - {epic: GOLD,       group: metals}
  - {epic: SILVER,     group: metals}
  - {epic: OIL_CRUDE,  group: energy}
  - {epic: NATURALGAS, group: energy}
  - {epic: COPPER,     group: metals}

monitoring:
  alerts: telegram             # telegram | email | none
  log_level: INFO
```

---

## 12. Project layout, stack, deliverables & acceptance

**Stack:** Python 3.11+, `httpx`/`requests` (REST), `websockets` (WS), `pandas`/`numpy`, `pydantic` (config), `APScheduler`/`asyncio` (scheduling), `SQLite`/`SQLAlchemy` (state), `pytest`, `plotly`/Chart.js or `streamlit` (reporting), `python-dotenv`.

**Layout**
```
seykota_bot/
  config/      config.yaml, .env.example, loader (pydantic)
  data/        capital + yfinance loaders, candle cache (SQLite/Parquet)
  broker/      capital_client.py (REST + WS, rate limiter)
  indicators/  ema, atr, donchian, swings
  strategy/    signal engine
  risk/        sizing, heat, drawdown, pyramiding, scaling
  engine/      backtester, live/paper orchestrator, state machine, scheduler
  reporting/   dashboard.py, eod_report.py (+ red-trade classifier)
  store/       positions, equity curve, trade log
  monitor/     logging, alerts, health checks
  cli.py       backtest | paper | live | report
  tests/
```

**Deliverables:** the package with working `backtest`, `paper` (demo), `live` modes behind a CLI; reusable rate-limited Capital.com client; pure unit-tested indicator/signal/risk functions; HTML dashboard + Markdown EOD report; `config.yaml` + `.env.example` + `README`; tests; and clear `TODO` markers wherever a Capital.com epic/field/host must be confirmed against the live API.

**Acceptance criteria**
1. Sizing math provably risks ≤ `risk_pct` of equity to the stop on every trade (unit test).
2. Portfolio heat never exceeds `heat_cap` (unit test).
3. No look-ahead in the backtest (shift-one-bar test cannot improve results).
4. Every paper/live position carries a **server-side protective stop**.
5. Graceful session re-auth and WS reconnect (simulated-failure test).
6. Demo run opens, trails, and closes a position end-to-end; EOD report classifies any losers.
7. Kill switch flattens all and halts.

**Build order:** data layer → indicators/signals → risk/sizing → backtester + metrics → dashboard + EOD report → Capital.com client (demo) → paper orchestrator + scheduler → guardrails/monitoring → (human-gated) live.

---

## Appendix A — Metric formulas
`r_t` = daily returns, P = 252.
- **CAGR** = `(End/Start)^(1/years) − 1`
- **Max Drawdown** = `min_t(equity_t/running_peak_t − 1)`
- **Sharpe** = `mean(r)/std(r) × √P`
- **Sortino** = `mean(r)/downside_dev × √P`, `downside_dev = √(mean(min(r,0)²))`
- **MAR/Calmar** = `CAGR/|MaxDD|`
- **Win rate** = `wins/trades` · **Profit factor** = `gross_win/|gross_loss|` · **Payoff** = `avg_win/|avg_loss|`
- **Expectancy ($)** = `win%·avg_win − loss%·|avg_loss|` · **Expectancy (R)** = same in units of initial risk.

## Appendix B — Capital.com gotchas
- Everything is a leveraged CFD; mind margin + overnight financing on held positions.
- Trading hours vary per instrument; gate on `marketStatus == TRADEABLE` (24×5; stocks only in session, crypto excluded to honour 24×5).
- WS caps at 40 instruments / 10-min session (ping to keep alive); stream drops on account switch.
- Back-adjusted/continuous data caveats for index/commodity CFDs; account for roll/financing, not phantom jumps.

## 13. Sources
**Seykota:** [DayTrading.com (Market Wizards quotes)](https://www.daytrading.com/ed-seykota) · [Seykota & Druz, "Determining Optimal Risk" (PDF)](https://www.trendfollowing.com/whitepaper/DETERMI.PDF) · [TrendFollowing.com / Whipsaw Song](https://www.trendfollowing.com/ed_seykota/) · [New Trader U — 6 Rules](https://www.newtraderu.com/2011/12/11/ed-seykotas-6-rules-from-the-whipsaw-song/) · [QuantifiedStrategies](https://www.quantifiedstrategies.com/ed-seykota-trading-strategies/)
**Trend-following / Turtle mechanics:** [Original Turtle Rules (PDF)](https://www.tradingwithrayner.com/wp-content/uploads/2014/11/OriginalTurtleRules.pdf) · [Position sizing](https://www.quantifiedstrategies.com/position-sizing-in-a-turtle-trading-system/) · [Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit) · [Backtest metrics](https://www.luxalgo.com/blog/top-7-metrics-for-backtesting-results/) · [Continuous futures caveats](https://quantpedia.com/continuous-futures-contracts-methodology-for-backtesting/)
**Capital.com API:** [Public API / Swagger](https://open-api.capital.com/) · [API guide](https://capital.com/en-int/trading-platforms/api-development-guide) · [REST + WebSocket](https://help.capital.com/hc/en-us/articles/6630762294418-Which-kind-of-APIs-do-you-have) · [CST & X-SECURITY-TOKEN](https://help.capital.com/hc/en-us/articles/5595698273298-How-can-I-get-CST-and-X-SECURITY-TOKEN-parameters) · [Rate limits](https://help.capital.com/hc/en-us/articles/6630830103058-Do-you-have-any-limitations-on-your-API) · [Postman collection](https://github.com/capital-com-sv/capital-api-postman)

*Reconstructed parameters (80/140 EMA, 50-bar breakout, 4×ATR) are community conventions consistent with Seykota's documented philosophy — validate by backtest, do not treat as his published system. Nothing here is financial advice.*
