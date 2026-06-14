# BUILD BRIEF — Seykota-Style Trend-Following Trading Bot (Capital.com)

**To:** Claude Code · **From:** Prajish Vinayak · **Version:** 1.0 (June 2026)
**Mandate:** Build this exactly. It is self-contained — everything you need (strategy, risk math, reference algorithms, Capital.com integration, architecture, config, tests, build order) is below. Tune only values in `config.yaml`. Nothing here is financial advice.

---

## 1. Mission & honest framing
Build a mechanical, rules-based **trend-following bot** that trades a diversified basket of **Capital.com CFDs** (equity indices, commodities/metals/energy, selected liquid stock/ETF CFDs) **24×5**, respecting each instrument's market hours. It must (a) generate signals from an EMA-trend-filter + Donchian-breakout + ATR-trailing-stop model, (b) size positions by volatility-scaled risk, (c) cap portfolio risk and de-risk in drawdowns, (d) be fully backtestable on **3 years of real data**, (e) **paper-trade on Capital.com demo**, and (f) produce a **dashboard** + a **detailed end-of-day report that explains every losing trade**.

Seykota's famous "~300,000%" was ~60%/yr compounded for 16+ years while surviving 50%+ drawdowns — the edge is **risk control + discipline**, not entries. Capital.com discloses ~78% of retail CFD accounts lose money. Engineer for survival, not a return promise. Expect a real trend-follower profile: **win rate ~35–45%, high payoff ratio**, long flat/down stretches between a few big winners. **Mandatory sequence: backtest → demo paper-trade → human-gated live. Never move real money automatically.**

**Non-goals:** intraday scalping, ML prediction, discretionary overrides (only a global kill switch).

---

## 2. The strategy — exact mechanical rules

**The 5 laws:** cut losses · ride winners · keep bets small · follow the rules · know when to break them (= kill switch only).

| # | Rule | Default |
|---|------|---------|
| R1 Universe | Trade only `TRADEABLE` instruments on closed candles (daily default; HOUR_4 optional). | — |
| R2 Trend filter | Long only if `EMA(fast) > EMA(slow)`; short only if `<`. Constraint `slow ≥ 3×fast`. | EMA 80/140 |
| R3 Entry | In gated direction, enter on new **N-bar Donchian breakout** (close ≥ prior N-bar high / ≤ prior N-bar low). | N=50 |
| R3b Fill | Signal on **closed bar t**, fill at **bar t+1 open**. No look-ahead. | — |
| R4 Initial stop | `2×N` from entry (`N=ATR(20)`), placed **server-side**; may tighten to recent swing; never loosened. | 2N |
| R5 Exit | **Chandelier trailing stop** only, ratcheting favourably; **no profit target**. | `HH(22)−3·ATR(22)` |
| R6 Risk/trade | Fixed % of **current** equity, sized via stop distance (§3). | 1% (cap 2%) |
| R7 Heat cap | Reject entries that push total open risk / equity above cap. | 15% |
| R8 Drawdown | −20% sizing equity per −10% DD; pause new entries past max DD. | pause @25% |
| R9 Price-action scaling | Volatility sizing (always) + structure stops + optional trend-strength & streak governors (§3.4). | — |
| R10 Pyramiding | Optional: +1 unit per +0.5N, max 4 units/instrument (6/correlated), trail combined stop. | off |
| R11 Guardrails | Per-trade cap, heat cap, margin check, daily loss limit, max-DD pause, kill switch (flatten+halt). | — |
| R12 Daily review | EOD report classifies every losing trade's reason so we iterate on **config**, never core rules mid-stream. | — |

**Research basis (sourced):** No academic papers exist on Seykota; sources are his *Market Wizards* interview, his 1993 *"Determining Optimal Risk"* (Seykota & Druz), the "Whipsaw Song," and the Turtle lineage. Genuinely-his and well-sourced: 1% risk, portfolio "heat" (*"setting the heat level matters far more than fiddling with trade timing"*), cut-losses/ride-winners, volatility sizing, "add smaller amounts on the way up." The exact parameters (80/140 EMA, 50-bar breakout, 4×ATR) are robust community reconstructions consistent with his philosophy — **validate by backtest, don't treat as published.** Sources in §10.

---

## 3. Risk & sizing engine (the heart — implement exactly)

**3.1 Volatility unit N** = `ATR(period)` with Wilder smoothing (EMA, α=1/period). TR = max(`H−L`, `|H−PrevClose|`, `|L−PrevClose|`).
```python
def atr_wilder(df, period):
    pc = df["close"].shift(1)
    tr = pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()
```

**3.2 Position size (volatility-scaled)**
```python
risk_amount   = sizing_equity * risk_pct                 # default 0.01
stop_distance = abs(entry_price - stop_price)            # = 2N by default (price points)
size          = risk_amount / (stop_distance * value_per_point_per_unit)
size          = floor(size / size_step) * size_step      # round DOWN to broker step
if size < min_deal_size:  skip_trade()                   # never round up
# verify free_margin >= size*price*margin_factor else reduce/skip
```
- `value_per_point_per_unit` = account-ccy P&L for a 1.0 price move on 1 unit (from `GET /markets/{epic}` + contract spec).
- **Size off *current* equity** so wins compound and losses de-leverage. Because stop = k·N, `size = (equity·risk_pct)/(k·N·value_per_point)` ⇒ **higher ATR → smaller size at constant dollar risk** (this is the price-action scaling).
- **Worked example:** equity $50k, risk 1% ⇒ $500. N=4, 2N stop=8 pts, value/pt=$10 ⇒ size=6.25; risk=6.25×8×10=$500=1%. If N doubles (8 ⇒ stop 16) ⇒ size=3.125 (halves automatically).

**3.3 Portfolio heat**
```python
position_risk_i = size_i * abs(price_i - stop_i) * value_per_point_i
heat = sum(position_risk_i) / equity
# reject new entry if (current_heat_$ + new_risk_$)/equity > heat_cap   # 0.15
```

**3.4 Price-action-scaled risk (layered; hard caps always win)**
1. Volatility scaling — built into 3.2 (always on).
2. Structure stops — tighten to recent swing: `stop = max(entry−2N, swing_low−0.1N)` (long).
3. Trend-strength scaling (optional) — scale `risk_pct` in `[risk_pct, risk_pct_max]` by `|EMA_fast−EMA_slow|/N` or ADX.
4. Streak/heat governor (optional) — trim `risk_pct` after consecutive losers / as heat nears cap. Never exceed the hard cap.

**3.5 Drawdown de-risking**
```python
dd = max(0, (peak_equity - equity)/peak_equity)
sizing_equity = equity * max(0.2, 1 - 2*floor(dd/0.10)*0.10)   # -20% per -10% DD
if dd >= max_drawdown_pause: block_new_entries()               # 0.25
```

**3.6 Pyramiding (optional):** +1 unit per +0.5N favourable, max 4 units/instrument; each add gets its own 2N stop; trail all units' stops up to the latest add (staircase).

---

## 4. Indicators & signals (reference)
```python
def build_indicators(df, cfg):
    o = df.copy()
    o["ema_fast"]=df.close.ewm(span=cfg.ema_fast,adjust=False).mean()
    o["ema_slow"]=df.close.ewm(span=cfg.ema_slow,adjust=False).mean()
    o["atr"]=atr_wilder(df,cfg.atr_period); o["atr_chan"]=atr_wilder(df,cfg.chandelier_period)
    o["donchian_hi"]=df.high.rolling(cfg.donchian_n).max().shift(1)   # PRIOR N bars
    o["donchian_lo"]=df.low.rolling(cfg.donchian_n).min().shift(1)
    o["hh_chan"]=df.high.rolling(cfg.chandelier_period).max()
    o["ll_chan"]=df.low.rolling(cfg.chandelier_period).min()
    o["swing_lo"]=df.low.rolling(cfg.swing_lookback).min()
    o["swing_hi"]=df.high.rolling(cfg.swing_lookback).max()
    o["uptrend"]=o.ema_fast>o.ema_slow
    return o
# entry (closed bar t, fill t+1 open):
#   long  = uptrend and close>=donchian_hi
#   short = (not uptrend) and close<=donchian_lo and allow_short
# trailing stop each bar (ratchet ONLY):
#   long : stop=max(prev_stop, hh_chan-chandelier_mult*atr_chan)
#   short: stop=min(prev_stop, ll_chan+chandelier_mult*atr_chan)
```

---

## 5. Capital.com integration (REST + WebSocket)
Everything is a **leveraged CFD**; size is CFD `size` (units), not shares; track margin + overnight financing.

- **Bases:** demo `https://demo-api-capital.backend-capital.com`, live `https://api-capital.backend-capital.com`, WS `wss://api-streaming-capital.backend-capital.com/connect`, prefix `/api/v1`. (Confirm against live Swagger.)
- **Auth:** `POST /session` with header `X-CAP-API-KEY` + body `{identifier, password}` → response headers give **`CST`** + **`X-SECURITY-TOKEN`**; send both on every request. Session expires after **10 min inactivity** → keepalive (`GET /session`/ping) + re-auth on 401. Optional encrypted-password flow via `GET /session/encryptionKey`.
- **Endpoints:** `GET /time` · `POST /session` · `GET/PUT /session` · `GET /accounts` · `GET /markets?searchTerm=` · `GET /markets/{epic}` (min size, step, margin, status, hours) · `GET /prices/{epic}?resolution=&max=&from=&to=` · `POST/GET/PUT/DELETE /positions[/{dealId}]` · `POST/GET/PUT/DELETE /workingorders` · `GET /confirms/{dealReference}`.
- **Resolutions:** `MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_4, DAY, WEEK`.
- **Orders:** body `{epic, direction: BUY|SELL, size, stopLevel|stopDistance, limitLevel|limitDistance, trailingStop, guaranteedStop}`. **Always send the protective stop with the order.** After `POST`, poll `GET /confirms/{dealReference}` for `dealId`; persist it.
- **Rate limits:** `POST /session` 1/sec; general & order creation ≤1 req/0.1s; demo `POST /positions` & `/workingorders` 1000/hour; WS **max 40 instruments / 10-min session** (ping to keep alive; resubscribe on reconnect; stream drops on account switch).

**Client interface to implement:**
```python
class CapitalClient:
    def login(self): ...            # POST /session; store CST + X-SECURITY-TOKEN
    def keepalive(self): ...        # GET /session; re-login on 401
    def market(self, epic)->dict    # min size, step, margin, status, hours
    def price_history(self, epic, resolution="DAY", start=None, end=None, max=1000)->list[dict]
        # [{snapshotTime,open,high,low,close,volume},...]; page backwards to cover 3y
    def open_position(self, epic, direction, size, stop_distance=None, stop_level=None,
                      trailing=False, guaranteed=False)->str   # returns dealId
    def amend_position(self, deal_id, stop_level=None, trailing=None): ...
    def close_position(self, deal_id): ...
    def positions(self)->list[dict]: ...
    def confirm(self, deal_reference)->dict: ...
# every request sends X-CAP-API-KEY, CST, X-SECURITY-TOKEN; wrap a token-bucket
# rate limiter (<=1 req/0.1s, <=1 session/s) + exponential backoff on 429/401.
```

---

## 6. Backtest engine (3 years real data)
**Requirements:** same code path as live for indicators/signals/risk; data from Capital.com `/prices` (DAY default) or yfinance fallback; **no look-ahead** (signal bar t → fill t+1 open; drop incomplete last candle); apply half-spread + slippage (≥1 tick) + overnight financing; **portfolio-level loop** (shared equity) so heat/drawdown are global.
```text
for date in calendar:
    fill_pending_entries(at open)              # from prior day's signals
    for each open position:
        accrue financing; update extreme; ratchet trailing stop
        if stop hit intraday: exit (gap-through -> fill at open); record trade
        elif exit_on_trend_flip and filter flipped: exit at close
    equity = balance + unrealized(close); peak=max(peak,equity); dd=(peak-equity)/peak
    if dd < max_drawdown_pause:
        for each flat instrument with breakout signal in trend direction:
            compute 2N(+structure) stop, size (§3.2), check heat/units/margin -> queue entry t+1
    record {date, equity, drawdown, open_positions, heat}
liquidate_open_at_last_close()
```
**Metrics:** total return, CAGR, max DD, Sharpe, Sortino, MAR/Calmar, num trades, win rate, profit factor, payoff ratio, avg win/loss, expectancy ($ and R), exposure, longest DD, per-instrument P&L (formulas in §9). **Validate:** walk-forward, uniform params across the basket (no over-optimization — Seykota's explicit warning).

---

## 7. Dashboard + End-of-day report

**Dashboard** — self-contained **HTML** (Chart.js from CDN, embedded JSON): metric cards; equity curve; drawdown + portfolio heat overlay; "why losing trades went RED" counts; P&L by instrument; full trades table. (Streamlit/Dash alternative OK, fed from `results.json`.)

**EOD report** (Markdown + optional HTML), per Rule 12: account snapshot (equity, drawdown, open positions, heat), today's opened/closed trades + realised P&L, open positions with stops & unrealised P&L, and **every losing trade classified by reason**:

| Code | Meaning |
|---|---|
| `WHIPSAW` | Breakout reversed within noise band in a few bars — expected cost; one trend pays for many. |
| `TREND_REVERSAL` | Trend filter flipped against the position; exit cut the loss. |
| `VOLATILITY_STOP` | ATR expanded; stop hit on a normal pullback — healthy risk control. |
| `GAP` | Gapped through stop (overnight/weekend) — consider guaranteed stops. |
| `FINANCING` | Small loss dominated by spread + overnight financing. |
| `RULE_OK_LOSS` | Textbook small loss the system is supposed to take. |
```python
def classify_loss(trade, ind):
    if trade.bars_held <= 5:                    return "WHIPSAW"
    if trend_filter_flipped(ind, trade):        return "TREND_REVERSAL"
    if trade.exit_reason == "STOP":             return "VOLATILITY_STOP"
    return "RULE_OK_LOSS"   # extend with GAP / FINANCING checks
```
End each report with the **config levers** to tune (`chandelier_mult`, `donchian_n`, `risk_pct`/`heat_cap`, `ema_fast/ema_slow`). Wire to a scheduled task per market close.

---

## 8. Live/paper loop, config, project, acceptance

**Paper/live loop (demo first):**
```text
on schedule (EOD per close, or HOUR_4 close intraday):
    client.keepalive(); reconcile GET /positions -> sync local state (broker = source of truth)
    recompute trailing stops -> PUT amend if tighter
    refresh candles (REST backfill + WS quotes, <=40 instruments)
    for each flat & TRADEABLE instrument with valid signal & passing risk checks:
        size (§3.2); POST /positions WITH server-side stop; confirm dealId; persist
    enforce guardrails (heat cap, daily loss limit, max-DD pause, max units)
    write EOD report + refresh dashboard
```
State machine per instrument `FLAT→PENDING_ENTRY→IN_POSITION→EXITING→FLAT`, persisted to SQLite. Idempotent (dealReference/confirms + locks). Resilient (re-auth 401, WS reconnect+resub, REST backoff, **server-side stops** so risk survives a crash). Demo/live is a config flag with an explicit human confirm for live.

**Config (`config.yaml` + `.env` secrets — no magic numbers in code):**
```yaml
account: {environment: demo, base_url_demo: ..., base_url_live: ..., ws_url: ...}  # creds in .env
mode: {run_mode: backtest, cadence: eod, intraday_resolution: HOUR_4}
strategy: {trend_filter: ema_pair, ema_fast: 80, ema_slow: 140, entry: donchian_breakout,
           donchian_n: 50, exit: chandelier, chandelier_period: 22, chandelier_mult: 3.0,
           seykota_atr_period: 100, seykota_atr_mult: 4.0, exit_on_trend_flip: false, allow_short: true}
risk: {starting_equity: 100000, atr_period: 20, risk_pct: 0.01, risk_pct_max: 0.02,
       initial_stop_atr_mult: 2.0, heat_cap: 0.15, max_units_per_instrument: 4,
       max_units_correlated: 6, drawdown_derisk: true, max_drawdown_pause: 0.25,
       trend_strength_scaling: false, streak_governor: true,
       pyramiding: {enabled: false, add_every_n: 0.5, max_adds: 3}}
execution: {slippage_ticks: 1, respect_market_hours: true, weekend_policy: hold, daily_loss_limit_pct: 0.06}
universe:  # confirm epics via GET /markets at build time
  - {epic: US500, group: equity_idx}
  - {epic: US100, group: equity_idx}
  - {epic: DE40,  group: equity_idx}
  - {epic: GOLD,  group: metals}
  - {epic: SILVER, group: metals}
  - {epic: OIL_CRUDE, group: energy}
  - {epic: NATURALGAS, group: energy}
  - {epic: COPPER, group: metals}
monitoring: {alerts: telegram, log_level: INFO}
```

**Stack:** Python 3.11+, httpx/requests, websockets, pandas/numpy, pydantic, APScheduler/asyncio, SQLite/SQLAlchemy, pytest, Chart.js/plotly or streamlit, python-dotenv.

**Layout:** `seykota_bot/{config,data,broker,indicators,strategy,risk,engine,reporting,store,monitor}` + `cli.py` (`backtest|paper|live|report`) + `tests/`.

**Acceptance criteria (must pass):**
1. Sizing risks ≤ `risk_pct` of equity to the stop on every trade (unit test).
2. Portfolio heat never exceeds `heat_cap` (unit test).
3. No look-ahead — a one-bar shift cannot improve backtest results (test).
4. Every paper/live position carries a server-side protective stop.
5. Graceful session re-auth + WS reconnect (simulated-failure test).
6. Demo run opens, trails, and closes a position end-to-end; EOD report classifies losers.
7. Kill switch flattens all and halts.

**Build order:** data layer → indicators/signals → risk/sizing → backtester + metrics → dashboard + EOD report → Capital.com client (demo) → paper orchestrator + scheduler → guardrails/monitoring → (human-gated) live.

---

## 9. Metric formulas
`r_t` daily returns, P=252. CAGR=`(End/Start)^(1/years)−1`; MaxDD=`min(equity/peak−1)`; Sharpe=`mean(r)/std(r)·√P`; Sortino=`mean(r)/downside_dev·√P`, `downside_dev=√(mean(min(r,0)²))`; MAR=`CAGR/|MaxDD|`; WinRate=`wins/trades`; ProfitFactor=`gross_win/|gross_loss|`; Payoff=`avg_win/|avg_loss|`; Expectancy$=`win%·avg_win−loss%·|avg_loss|`.

## 10. Sources
**Seykota:** [DayTrading.com (Market Wizards quotes)](https://www.daytrading.com/ed-seykota) · [Seykota & Druz, "Determining Optimal Risk" PDF](https://www.trendfollowing.com/whitepaper/DETERMI.PDF) · [TrendFollowing.com / Whipsaw Song](https://www.trendfollowing.com/ed_seykota/) · [New Trader U — 6 Rules](https://www.newtraderu.com/2011/12/11/ed-seykotas-6-rules-from-the-whipsaw-song/) · [QuantifiedStrategies](https://www.quantifiedstrategies.com/ed-seykota-trading-strategies/)
**Turtle/trend mechanics:** [Original Turtle Rules PDF](https://www.tradingwithrayner.com/wp-content/uploads/2014/11/OriginalTurtleRules.pdf) · [Position sizing](https://www.quantifiedstrategies.com/position-sizing-in-a-turtle-trading-system/) · [Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit) · [Backtest metrics](https://www.luxalgo.com/blog/top-7-metrics-for-backtesting-results/)
**Capital.com:** [Public API/Swagger](https://open-api.capital.com/) · [API guide](https://capital.com/en-int/trading-platforms/api-development-guide) · [REST+WebSocket](https://help.capital.com/hc/en-us/articles/6630762294418-Which-kind-of-APIs-do-you-have) · [CST & X-SECURITY-TOKEN](https://help.capital.com/hc/en-us/articles/5595698273298-How-can-I-get-CST-and-X-SECURITY-TOKEN-parameters) · [Rate limits](https://help.capital.com/hc/en-us/articles/6630830103058-Do-you-have-any-limitations-on-your-API) · [Postman collection](https://github.com/capital-com-sv/capital-api-postman)

*Reconstructed parameters (80/140 EMA, 50-bar breakout, 4×ATR) are community conventions consistent with Seykota's documented philosophy — validate by backtest. Not financial advice. Demo/paper first; live only as a deliberate human decision.*
