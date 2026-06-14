#!/usr/bin/env python3
"""
Multi-market backtest: run the REFINED Seykota config across ~40 markets spanning
indices, metals, energy, agriculture, FX and single stocks — then break the result
down per asset-class and per market, so we can see WHERE the edge actually lives
and refine the universe (not curve-fit parameters).

Real daily data via yfinance, cached to ./cache/*.pkl. Run:  python multi_market.py
"""
from __future__ import annotations
import os, json, collections, copy
import numpy as np
import pandas as pd
from seykota_bot.engine import Config, Instrument, Backtest
from seykota_bot.data import load_yfinance

CACHE = "cache"
YEARS = 3

# epic -> (yfinance ticker, asset group)
UNIVERSE = {
    # --- equity indices ---
    "US500": ("ES=F", "index"), "US100": ("NQ=F", "index"),
    "US30": ("YM=F", "index"), "RUSSELL": ("RTY=F", "index"),
    "DE40": ("^GDAXI", "index"), "UK100": ("^FTSE", "index"),
    "JP225": ("^N225", "index"), "EU50": ("^STOXX50E", "index"),
    # --- metals ---
    "GOLD": ("GC=F", "metal"), "SILVER": ("SI=F", "metal"),
    "COPPER": ("HG=F", "metal"), "PLATINUM": ("PL=F", "metal"),
    "PALLADIUM": ("PA=F", "metal"),
    # --- energy ---
    "WTI": ("CL=F", "energy"), "BRENT": ("BZ=F", "energy"),
    "NATGAS": ("NG=F", "energy"), "GASOLINE": ("RB=F", "energy"),
    "HEATOIL": ("HO=F", "energy"),
    # --- agriculture / softs ---
    "WHEAT": ("ZW=F", "ag"), "CORN": ("ZC=F", "ag"),
    "SOYBEAN": ("ZS=F", "ag"), "COFFEE": ("KC=F", "ag"),
    "SUGAR": ("SB=F", "ag"), "COTTON": ("CT=F", "ag"),
    "COCOA": ("CC=F", "ag"), "CATTLE": ("LE=F", "ag"),
    # --- FX majors ---
    "EURUSD": ("EURUSD=X", "fx"), "GBPUSD": ("GBPUSD=X", "fx"),
    "USDJPY": ("JPY=X", "fx"), "AUDUSD": ("AUDUSD=X", "fx"),
    "USDCAD": ("CAD=X", "fx"), "USDCHF": ("CHF=X", "fx"),
    "NZDUSD": ("NZDUSD=X", "fx"),
    # --- single stocks (Capital.com offers these as CFDs) ---
    "AAPL": ("AAPL", "stock"), "MSFT": ("MSFT", "stock"),
    "NVDA": ("NVDA", "stock"), "AMZN": ("AMZN", "stock"),
    "TSLA": ("TSLA", "stock"), "META": ("META", "stock"),
}
TICKER_MAP = {e: t for e, (t, g) in UNIVERSE.items()}
GROUP = {e: g for e, (t, g) in UNIVERSE.items()}


def load_all():
    os.makedirs(CACHE, exist_ok=True)
    data, missing = {}, []
    for e in UNIVERSE:
        p = os.path.join(CACHE, f"{e}.pkl")
        if os.path.exists(p):
            data[e] = pd.read_pickle(p)
        else:
            missing.append(e)
    if missing:
        fresh = load_yfinance(missing, years=YEARS, ticker_map=TICKER_MAP)
        for e, df in fresh.items():
            df.to_pickle(os.path.join(CACHE, f"{e}.pkl"))
            data[e] = df
    return data


def make_instruments(data):
    """value_per_point is risk-neutral here (size = risk/(stop*vpp) cancels in R-terms);
    spread/financing are scaled to each market's own price so costs are realistic."""
    inst = {}
    for e, df in data.items():
        px = float(df["close"].median())
        g = GROUP[e]
        spread_bp = {"fx": 0.0001, "index": 0.0002, "stock": 0.0003,
                     "metal": 0.0002, "energy": 0.0004, "ag": 0.0004}.get(g, 0.0003)
        inst[e] = Instrument(
            epic=e, group=g, value_per_point=1.0, min_size=0.01, size_step=0.01,
            spread_points=px * spread_bp,
            financing_daily=px * 1.0 * 0.00008,
            corr_group="equity" if g in ("index", "stock") else g,
        )
    return inst


def run(cfg, instruments, data):
    inst = {k: v for k, v in instruments.items() if k in data}
    return Backtest(cfg, inst, data).run()


def main():
    os.makedirs("output", exist_ok=True)
    data = load_all()
    print(f"\nLoaded {len(data)}/{len(UNIVERSE)} markets of REAL daily data "
          f"({min(d.index.min() for d in data.values()).date()} -> "
          f"{max(d.index.max() for d in data.values()).date()})")
    by_grp = collections.Counter(GROUP[e] for e in data)
    print("  by class: " + ", ".join(f"{k}:{v}" for k, v in by_grp.items()))

    instruments = make_instruments(data)
    cfg = Config()  # refined defaults (100/200, chandelier 3.5, shorts on)

    for label, c in [("shorts ON (default)", cfg),
                     ("LONG-only", copy.copy(cfg))]:
        if label == "LONG-only":
            c.allow_short = False
        res = run(c, instruments, data)
        m = res["metrics"]
        print(f"\n=== {len(data)}-market portfolio — {label} ===")
        print(f"  return {m['total_return_pct']}%  CAGR {m['cagr_pct']}%  maxDD {m['max_drawdown_pct']}%  "
              f"Sharpe {m['sharpe']}  PF {m['profit_factor']}  win {m['win_rate_pct']}%  "
              f"payoff {m['payoff_ratio']}  trades {m['num_trades']}")
        if label == "shorts ON (default)":
            default_res = res

    # per-class & per-market breakdown for the default (shorts on)
    trades = default_res["trades"]
    grp_pnl = collections.defaultdict(lambda: [0.0, 0, 0])
    mkt_pnl = collections.defaultdict(lambda: [0.0, 0, 0])
    for t in trades:
        g = GROUP.get(t["epic"], "?")
        for d, key in ((grp_pnl[g], g), (mkt_pnl[t["epic"]], t["epic"])):
            d[0] += t["pnl"]; d[1] += 1; d[2] += 1 if t["pnl"] > 0 else 0

    print("\n--- P&L by ASSET CLASS (shorts on) ---")
    for g, v in sorted(grp_pnl.items(), key=lambda kv: -kv[1][0]):
        wr = 100 * v[2] / v[1] if v[1] else 0
        print(f"  {g:8} {v[0]:>11,.0f}   ({v[1]:>3} trades, {wr:.0f}% win)")

    print("\n--- best & worst MARKETS ---")
    ranked = sorted(mkt_pnl.items(), key=lambda kv: -kv[1][0])
    for e, v in ranked[:8]:
        print(f"  + {e:10} {v[0]:>10,.0f}  ({v[1]} trades)")
    print("  ...")
    for e, v in ranked[-6:]:
        print(f"  - {e:10} {v[0]:>10,.0f}  ({v[1]} trades)")

    winners = sum(1 for e, v in mkt_pnl.items() if v[0] > 0)
    print(f"\n  {winners}/{len(mkt_pnl)} markets net-profitable. "
          f"Edge breadth is what matters for trend-following robustness.")

    with open(os.path.join("output", "multi_market.json"), "w") as f:
        json.dump({"metrics": default_res["metrics"],
                   "by_class": {g: v for g, v in grp_pnl.items()},
                   "by_market": {e: v for e, v in mkt_pnl.items()}}, f, indent=2, default=str)
    print("\n  -> output/multi_market.json")

    # ---- PRODUCTION run: refined universe (no FX) + refined config (long-only) ---- #
    from seykota_bot.dashboard import build_dashboard
    from seykota_bot.eod_report import daily_report
    prod_data = {e: d for e, d in data.items() if GROUP[e] != "fx"}
    prod_cfg = Config()  # long-only, 100/200, chandelier 3.5
    prod = run(prod_cfg, instruments, prod_data)
    pm = prod["metrics"]
    print(f"\n=== PRODUCTION universe ({len(prod_data)} markets, no FX) + long-only ===")
    print(f"  return {pm['total_return_pct']}%  CAGR {pm['cagr_pct']}%  maxDD {pm['max_drawdown_pct']}%  "
          f"Sharpe {pm['sharpe']}  PF {pm['profit_factor']}  win {pm['win_rate_pct']}%  trades {pm['num_trades']}")
    with open(os.path.join("output", "results.json"), "w") as f:
        json.dump(prod, f, indent=2, default=str)
    build_dashboard(prod, os.path.join("output", "dashboard.html"),
                    title=f"Seykota Bot — {len(prod_data)}-market REAL backtest (no FX · long-only · 3y)")
    daily_report(prod, md_path=os.path.join("output", "eod_report.md"))
    print("  -> output/dashboard.html, results.json, eod_report.md (PRODUCTION)")


if __name__ == "__main__":
    main()
