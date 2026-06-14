#!/usr/bin/env python3
"""
Backtest the refined Seykota config on CAPITAL.COM's OWN daily prices — the exact
feed the bot will trade on. Resolves Capital epic names (with a search fallback),
caches to ./cache_capital/*.pkl, runs the production config (no FX, long-only),
and regenerates the dashboard / EOD report / results.json from broker data.

Run:  python capital_backtest.py
"""
from __future__ import annotations
import os, json, collections
import pandas as pd
from seykota_bot.capital_client import CapitalClient, CapitalError
from seykota_bot.data import clean_spikes
from seykota_bot.engine import Config, Instrument, Backtest
from seykota_bot.dashboard import build_dashboard
from seykota_bot.eod_report import daily_report

CACHE = "cache_capital"

# desired universe: label -> (candidate Capital epic, asset group, search term)
# FX excluded by design (range-bound -> poor for breakout trend-following).
WANT = {
    # indices
    "US500": ("US500", "index", "US 500"), "US100": ("US100", "index", "US Tech 100"),
    "US30": ("US30", "index", "Wall Street"), "DE40": ("DE40", "index", "Germany 40"),
    "UK100": ("UK100", "index", "UK 100"), "FR40": ("FR40", "index", "France 40"),
    "EU50": ("EU50", "index", "Euro 50"), "JP225": ("J225", "index", "Japan 225"),
    # metals
    "GOLD": ("GOLD", "metal", "Gold"), "SILVER": ("SILVER", "metal", "Silver"),
    "COPPER": ("COPPER", "metal", "Copper"), "PLATINUM": ("PLATINUM", "metal", "Platinum"),
    "PALLADIUM": ("PALLADIUM", "metal", "Palladium"),
    # energy
    "OIL_CRUDE": ("OIL_CRUDE", "energy", "Crude Oil"), "OIL_BRENT": ("OIL_BRENT", "energy", "Brent"),
    "NATURALGAS": ("NATURALGAS", "energy", "Natural Gas"),
    # agriculture / softs
    "WHEAT": ("WHEAT", "ag", "Wheat"), "CORN": ("CORN", "ag", "Corn"),
    "SOYBEAN": ("SOYBEAN", "ag", "Soybean"), "COFFEE": ("COFFEE", "ag", "Coffee"),
    "SUGAR": ("SUGAR", "ag", "Sugar"), "COCOA": ("COCOA", "ag", "Cocoa"),
    "COTTON": ("COTTON", "ag", "Cotton"),
    # single stocks (CFDs)
    "AAPL": ("AAPL", "stock", "Apple"), "MSFT": ("MSFT", "stock", "Microsoft"),
    "NVDA": ("NVDA", "stock", "Nvidia"), "AMZN": ("AMZN", "stock", "Amazon"),
    "TSLA": ("TSLA", "stock", "Tesla"), "META": ("META", "stock", "Meta"),
}


def resolve_epic(client, label, candidate, term):
    """Return a Capital epic that yields data, or None. Tries candidate then search."""
    for epic in [candidate]:
        try:
            rows = client.price_history(epic, resolution="DAY", max=10)
            if rows:
                return candidate
        except CapitalError:
            pass
    # search fallback — prefer an exact-ish epic, then anything that returns data
    try:
        hits = client.search_markets(term)
    except Exception:
        hits = []
    for h in hits[:6]:
        ep = h.get("epic")
        if not ep:
            continue
        try:
            rows = client.price_history(ep, resolution="DAY", max=10)
            if rows:
                return ep
        except CapitalError:
            continue
    return None


def load_capital_universe(client):
    os.makedirs(CACHE, exist_ok=True)
    data, groups, resolved = {}, {}, {}
    for label, (cand, grp, term) in WANT.items():
        p = os.path.join(CACHE, f"{label}.pkl")
        if os.path.exists(p):
            data[label] = pd.read_pickle(p); groups[label] = grp
            continue
        epic = resolve_epic(client, label, cand, term)
        if not epic:
            print(f"  {label:11} -> NOT FOUND on Capital (skipped)")
            continue
        rows = client.price_history(epic, resolution="DAY")
        if len(rows) < 200:
            print(f"  {label:11} -> only {len(rows)} candles via {epic} (skipped)")
            continue
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["snapshotTime"])
        df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna()
        df, nfix = clean_spikes(df)   # repair demo-feed bad ticks
        df.to_pickle(p)
        data[label] = df; groups[label] = grp; resolved[label] = epic
        tag = "" if epic == cand else f" (via search: {epic})"
        spk = f"  [cleaned {nfix} spike(s)]" if nfix else ""
        print(f"  {label:11} -> {len(df)} candles {df.index[0].date()}->{df.index[-1].date()}{tag}{spk}")
    return data, groups


def make_instruments(data, groups):
    inst = {}
    for e, df in data.items():
        px = float(df["close"].median()); g = groups[e]
        spread_bp = {"index": 0.0002, "stock": 0.0003, "metal": 0.0002,
                     "energy": 0.0004, "ag": 0.0004}.get(g, 0.0003)
        inst[e] = Instrument(epic=e, group=g, value_per_point=1.0, min_size=0.01,
                             size_step=0.01, spread_points=px * spread_bp,
                             financing_daily=px * 0.00008,
                             corr_group="equity" if g in ("index", "stock") else g)
    return inst


def main():
    client = CapitalClient.from_env()
    client.login()
    print(f"Logged in ({client.environment}). Resolving + pulling Capital.com daily data:\n")
    data, groups = load_capital_universe(client)
    if not data:
        print("No data resolved — aborting.")
        return
    print(f"\nLoaded {len(data)} markets from Capital.com "
          f"({min(d.index.min() for d in data.values()).date()} -> "
          f"{max(d.index.max() for d in data.values()).date()})")
    by_grp = collections.Counter(groups[e] for e in data)
    print("  by class: " + ", ".join(f"{k}:{v}" for k, v in by_grp.items()))

    inst = make_instruments(data, groups)
    cfg = Config()  # refined production defaults: long-only, 100/200, chandelier 3.5
    res = Backtest(cfg, inst, data).run()
    m = res["metrics"]
    print(f"\n=== CAPITAL.COM DATA — {len(data)} markets, refined config (long-only) ===")
    for k in ["total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "mar_calmar",
              "num_trades", "win_rate_pct", "profit_factor", "payoff_ratio", "expectancy_per_trade"]:
        print(f"  {k:>22}: {m.get(k)}")

    grp_pnl = collections.defaultdict(lambda: [0.0, 0, 0])
    for t in res["trades"]:
        g = groups.get(t["epic"], "?"); grp_pnl[g][0] += t["pnl"]
        grp_pnl[g][1] += 1; grp_pnl[g][2] += 1 if t["pnl"] > 0 else 0
    print("\n  P&L by class:")
    for g, v in sorted(grp_pnl.items(), key=lambda kv: -kv[1][0]):
        print(f"    {g:8} {v[0]:>11,.0f}  ({v[1]} trades, {100*v[2]/v[1] if v[1] else 0:.0f}% win)")

    os.makedirs("output", exist_ok=True)
    with open("output/results_capital.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    build_dashboard(res, "output/dashboard_capital.html",
                    title=f"Seykota Bot — {len(data)}-market backtest on CAPITAL.COM data (long-only · 3y)")
    daily_report(res, md_path="output/eod_report_capital.md")
    print("\n  -> output/dashboard_capital.html, results_capital.json, eod_report_capital.md")


if __name__ == "__main__":
    main()
