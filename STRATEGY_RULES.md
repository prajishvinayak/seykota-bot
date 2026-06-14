# Seykota-Style Trading Bot — The Rulebook

These are the exact, mechanical rules the bot executes. No discretion, no prediction, no overrides. Every rule maps to code in `seykota_bot/`. Parameters live in `config.yaml`; the logic below is fixed.

> **Reality check (read once, remember always).** Ed Seykota compounded ~60%/yr for ~16+ years (the "300,000%") using leverage and by *surviving* drawdowns >50%. The edge is **risk control + discipline through losing streaks**, not clever entries. Capital.com states ~78% of retail CFD accounts lose money. This rulebook is engineered to put the odds on the right side of that statistic; it does not promise profit. Backtest, then paper-trade, before any real money.

---

## Rule 0 — Philosophy (the 5 laws)
1. **Cut losses.** 2. **Ride winners.** 3. **Keep bets small.** 4. **Follow the rules without question.** 5. **Know when to break the rules** (the only human override: a global kill switch).

Expect a **low win rate (~30–45%)** and a **high payoff ratio**. Most trades are small losses; a few big trends pay for everything. Do not judge the system by individual trades or short windows.

---

## Rule 1 — What we trade
- Diversified basket of Capital.com CFDs across uncorrelated sectors: equity indices, commodities (metals, energy), and selected liquid stock/ETF CFDs.
- Trade only when an instrument's market status is `TRADEABLE` (24×5; respect each instrument's hours).
- Trade on **closed candles only** (daily by default; HOUR_4 optional). Never act on a forming bar.

## Rule 2 — Trend filter (direction gate)
- **Uptrend** when `EMA(80) > EMA(140)` → longs allowed, shorts blocked.
- **Downtrend** when `EMA(80) < EMA(140)` → shorts allowed, longs blocked.
- Constraint: slow EMA ≥ 3× fast EMA (whipsaw suppression). Never trade against the gate.

## Rule 3 — Entry (only in the gated direction)
- **Long** when the close makes a new **50-bar high** (Donchian breakout) **and** trend filter = uptrend.
- **Short** when the close makes a new **50-bar low and** trend filter = downtrend.
- Signal on bar *t* close → execute on bar *t+1* (no look-ahead). One entry per signal.
- Reject the entry if it would breach any risk cap (Rules 6–8).

## Rule 4 — Initial protective stop (cut losses)
- Set **at entry**, server-side on the broker, at **2 × N** from entry (`N = ATR(20)`):
  - Long stop = `entry − 2N`; Short stop = `entry + 2N`.
- May be tightened to the most recent swing low/high if that is closer (price-action stop, Rule 9).
- A stop is **never loosened**. Ever.

## Rule 5 — Exit (ride winners)
- No profit targets. Exit **only** when the trailing stop is hit.
- **Trailing stop (default = Chandelier):** Long = `HighestHigh(22) − 3×ATR(22)`, ratcheting up only; Short = `LowestLow(22) + 3×ATR(22)`, ratcheting down only.
- Optional wide "Seykota" trail: exit when price closes beyond `4×ATR(100)` from the trade's running extreme.
- Optional exit on trend-filter flip (config).

## Rule 6 — Position size (volatility-scaled, the core of the edge)
Risk a fixed, small fraction of **current** equity per trade; convert to size via the stop distance:
```
risk_amount   = equity × risk_pct                 # default risk_pct = 1%
stop_distance = |entry − initial_stop|            # in points (= 2N by default)
size          = risk_amount / (stop_distance × value_per_point_per_unit)
size          = round_down_to_broker_step(size)   # skip trade if < min_deal_size
```
- This makes **size shrink automatically when volatility rises** and grow when it falls — risk stays constant. (See worked example in the brief.)
- Size off **current equity** so wins compound and losses de-leverage automatically.
- Always confirm free margin ≥ required margin before sending; otherwise reduce or skip.

## Rule 7 — Portfolio heat (total open risk cap)
- `heat = Σ(open position risk to its stop) / equity`.
- Reject any new entry that would push projected heat above the cap (**default 15%**; tune 10–20%). Seykota: setting the heat level matters more than entry timing.

## Rule 8 — Drawdown de-risking (survive losing streaks)
- For every **−10%** from peak equity, cut the **sizing equity by 20%**; restore as equity recovers.
- Pause **all new entries** if drawdown exceeds `max_drawdown_pause` (default 25%). Existing positions keep their stops.

## Rule 9 — Scalable risk based on price action (layered)
Position risk dynamically responds to the chart. All layers can only move risk *within* the configured safe band; hard caps always win.
1. **Volatility scaling (always on):** built into Rule 6 — high ATR ⇒ smaller size.
2. **Structure stops:** anchor the stop to recent swing high/low when tighter ⇒ tighter risk, right-sized size.
3. **Trend-strength scaling (optional):** raise `risk_pct` toward `risk_pct_max` when the trend is strong (e.g. ADX high / wide EMA spread in N units), lower it in choppy conditions.
4. **Streak/heat governor (optional):** trim `risk_pct` after consecutive losers or as heat nears its cap. Never increases risk above the cap.

## Rule 10 — Pyramiding (optional, off by default)
- Add 1 unit per **+0.5N** in favor, up to **4 units** per instrument.
- Each add gets its own 2N stop; trail **all** units' stops up to the latest add (staircase) so the stack carries one tight stop.

## Rule 11 — Hard guardrails (enforced in code)
- Per-trade risk cap, heat cap, margin check, max units (4/instrument, 6/correlated group), daily loss limit, max-drawdown pause, and a **global kill switch** (flatten-all + halt).
- Broker-side stops on every position so risk persists even if the bot disconnects.
- Demo/paper first. Live is an explicit, human-confirmed config flip.

## Rule 12 — Daily review loop (how we improve it together)
At the end of each session the bot produces a **detailed EOD report**: every trade taken/closed, current open positions, equity & drawdown, portfolio heat — and for **every losing (RED) trade, the reason**, classified as one of:
- `WHIPSAW` — entered on a breakout that reversed within the noise band (expected cost; one trend pays for many).
- `TREND_REVERSAL` — trend filter flipped against the position after entry.
- `VOLATILITY_STOP` — ATR expanded and the trailing/initial stop was hit on a normal pullback.
- `GAP` — overnight/weekend gap jumped the stop (slippage beyond stop level).
- `TIME_DECAY/FINANCING` — small loss driven mostly by spread + overnight financing on a flat trade.
- `RULE_OK_LOSS` — a textbook small loss the system is *supposed* to take; no change needed.
We review the report together, adjust **parameters in config** (never the core rules mid-stream — Rule 0.4), and iterate.

---

### One-line summary
*Trade only with the trend, enter on breakouts, cut every loss at 2N, ride winners on a 3×ATR trailing stop, risk ~1% of current equity per trade sized to volatility, cap total heat, de-risk in drawdowns — and survive.*
