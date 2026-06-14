# Daily Trading Report — 2026-06-12

_Seykota-style trend-following bot. Not financial advice._

## Account snapshot
- Equity: **188,053.92**
- Drawdown from peak: **-10.75%**
- Open positions: **1**
- Portfolio heat (open risk / equity): **1.65%**
- Realised P&L on closed trades today: **1,755.98**

## Activity today
- Trades opened: 0  |  closed: 1 (winners 1, losers 0)

| Instrument | Side | Entry | Exit | Bars | P&L | Exit | Red reason |
|---|---|---|---|---|---|---|---|
| US30 | LONG | 50410.4588 | 51605.0 | 15 | 1,755.98 | END |  |

## Why trades went RED today
_No losing trades today._

## Iterate together
Adjust **parameters in config** (never the core rules mid-stream). Common levers: `chandelier_mult` (exit tightness), `donchian_n` (entry sensitivity), `risk_pct`/`heat_cap` (aggressiveness), `ema_fast/ema_slow` (trend speed).