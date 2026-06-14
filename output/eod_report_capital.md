# Daily Trading Report — 2026-06-12

_Seykota-style trend-following bot. Not financial advice._

## Account snapshot
- Equity: **225,062.81**
- Drawdown from peak: **-6.92%**
- Open positions: **1**
- Portfolio heat (open risk / equity): **1.00%**
- Realised P&L on closed trades today: **-11.59**

## Activity today
- Trades opened: 1  |  closed: 1 (winners 0, losers 1)

| Instrument | Side | Entry | Exit | Bars | P&L | Exit | Red reason |
|---|---|---|---|---|---|---|---|
| EU50 | LONG | 6186.90452 | 6185.8 | 1 | -11.59 | END | WHIPSAW |

## Why trades went RED today
### WHIPSAW — 1 trade(s), -11.59
Breakout reversed inside the noise band within a few bars. Expected cost of trend following — one large winner is designed to pay for many of these. No action needed unless the rate is abnormally high.
- EU50 LONG: held 1 bars, lost -11.59 (risked ~2,257.07 pts of stop).

## Iterate together
Adjust **parameters in config** (never the core rules mid-stream). Common levers: `chandelier_mult` (exit tightness), `donchian_n` (entry sensitivity), `risk_pct`/`heat_cap` (aggressiveness), `ema_fast/ema_slow` (trend speed).