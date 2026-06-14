"""
End-of-day report. Summarises the session's trades, open positions, equity,
and — per Rule 12 — explains every losing (RED) trade so we can iterate.

Works for both backtest output and live/paper output (same trade schema).
"""
from __future__ import annotations
from collections import Counter
from datetime import date as _date

RED_EXPLANATIONS = {
    "WHIPSAW": "Breakout reversed inside the noise band within a few bars. Expected cost of trend following — one large winner is designed to pay for many of these. No action needed unless the rate is abnormally high.",
    "TREND_REVERSAL": "The EMA trend filter flipped against the position after entry. The trailing/trend-flip exit did its job cutting the loss. Consider whether the trend filter is too slow for this instrument.",
    "VOLATILITY_STOP": "ATR expanded and the trailing stop was hit on a normal pullback. Healthy risk control. If frequent, the chandelier multiple may be too tight for this instrument's volatility.",
    "GAP": "Price gapped through the stop (overnight/weekend), so the fill was worse than the stop level. Unavoidable on CFDs without guaranteed stops; consider guaranteed stops on gap-prone names.",
    "RULE_OK_LOSS": "A textbook small loss the system is supposed to take. This is the engine working as designed.",
    "UNSPEC": "Unclassified small loss — review the trade manually.",
}


def daily_report(results: dict, on_date: str | None = None, md_path: str | None = None) -> str:
    eq = results["equity_curve"]
    trades = results["trades"]
    if not eq:
        return "No data."
    on_date = on_date or eq[-1]["date"]
    last = eq[-1]

    closed_today = [t for t in trades if t["exit_date"] == on_date]
    opened_today = [t for t in trades if t["entry_date"] == on_date]
    reds = [t for t in closed_today if t["pnl"] < 0]
    greens = [t for t in closed_today if t["pnl"] >= 0]
    day_pnl = sum(t["pnl"] for t in closed_today)

    L = []
    L.append(f"# Daily Trading Report — {on_date}\n")
    L.append("_Seykota-style trend-following bot. Not financial advice._\n")
    L.append("## Account snapshot")
    L.append(f"- Equity: **{last['equity']:,.2f}**")
    L.append(f"- Drawdown from peak: **{last['drawdown']*100:.2f}%**")
    L.append(f"- Open positions: **{last['open_positions']}**")
    L.append(f"- Portfolio heat (open risk / equity): **{last['heat']*100:.2f}%**")
    L.append(f"- Realised P&L on closed trades today: **{day_pnl:,.2f}**\n")

    L.append("## Activity today")
    L.append(f"- Trades opened: {len(opened_today)}  |  closed: {len(closed_today)} "
             f"(winners {len(greens)}, losers {len(reds)})\n")

    if closed_today:
        L.append("| Instrument | Side | Entry | Exit | Bars | P&L | Exit | Red reason |")
        L.append("|---|---|---|---|---|---|---|---|")
        for t in closed_today:
            side = "LONG" if t["direction"] > 0 else "SHORT"
            L.append(f"| {t['epic']} | {side} | {t['entry_price']} | {t['exit_price']} | "
                     f"{t['bars_held']} | {t['pnl']:,.2f} | {t['exit_reason']} | {t.get('red_reason','')} |")
        L.append("")

    # RED analysis (Rule 12)
    L.append("## Why trades went RED today")
    if not reds:
        L.append("_No losing trades today._\n")
    else:
        counts = Counter(t.get("red_reason", "UNSPEC") for t in reds)
        for reason, c in counts.most_common():
            L.append(f"### {reason} — {c} trade(s), "
                     f"{sum(t['pnl'] for t in reds if t.get('red_reason')==reason):,.2f}")
            L.append(RED_EXPLANATIONS.get(reason, RED_EXPLANATIONS['UNSPEC']))
            for t in reds:
                if t.get("red_reason") == reason:
                    L.append(f"- {t['epic']} {('LONG' if t['direction']>0 else 'SHORT')}: "
                             f"held {t['bars_held']} bars, lost {t['pnl']:,.2f} "
                             f"(risked ~{abs(t['mae'])*t['size']:,.2f} pts of stop).")
            L.append("")

    # current open positions
    open_pos = [t for t in trades if False]  # backtest closes all; live fills this
    if results.get("open_positions"):
        L.append("## Open positions (carried overnight)")
        L.append("| Instrument | Side | Entry | Stop | Unrealised |")
        L.append("|---|---|---|---|---|")
        for p in results["open_positions"]:
            L.append(f"| {p['epic']} | {'LONG' if p['direction']>0 else 'SHORT'} | "
                     f"{p['entry_price']} | {p['stop_price']} | {p.get('unrealized','-')} |")
        L.append("")

    L.append("## Iterate together")
    L.append("Adjust **parameters in config** (never the core rules mid-stream). "
             "Common levers: `chandelier_mult` (exit tightness), `donchian_n` (entry sensitivity), "
             "`risk_pct`/`heat_cap` (aggressiveness), `ema_fast/ema_slow` (trend speed).")

    report = "\n".join(L)
    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report)
    return report
