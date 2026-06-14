#!/usr/bin/env python3
"""
Add Asian-session indices to the universe and backtest the impact on Capital.com data.
Checks their correlation with the Western index basket to decide the correlation cluster
(if they're uncorrelated they get their own 'equity_asia' cluster so they diversify
rather than fight the max-3-per-cluster cap).

Run:  python asian_indices.py
"""
from __future__ import annotations
import os, collections
import numpy as np, pandas as pd
from seykota_bot.capital_client import CapitalClient
from seykota_bot.engine import Config, Instrument, Backtest
from seykota_bot.data import clean_spikes
from capital_backtest import resolve_epic, WANT
from seykota_bot.dashboard import build_dashboard
from seykota_bot.eod_report import daily_report

CACHE = "cache_capital"

# Asian-session index candidates (resolve_epic searches if the epic guess is wrong)
ASIAN = {
    # only genuine, correctly-resolved Asian-session INDICES (India=ETF, Taiwan=stock dropped)
    "J225":   ("J225",   "Japan 225"),     # already traded — re-clustered into Asia
    "HK50":   ("HK50",   "Hong Kong"),
    "AUS200": ("AU200",  "Australia 200"),
    "CHINA50":("CN50",   "China A50"),
    "SINGAPORE":("SG25", "Singapore 25"),
}
WESTERN_IDX = ["US500", "US100", "US30", "DE40", "UK100", "FR40", "EU50"]


def pull(client, label, cand, term):
    p = os.path.join(CACHE, f"{label}.pkl")
    if os.path.exists(p):
        return pd.read_pickle(p)
    epic = resolve_epic(client, label, cand, term)
    if not epic:
        print(f"  {label:10} -> NOT FOUND"); return None
    rows = client.price_history(epic, resolution="DAY")
    if len(rows) < 200:
        print(f"  {label:10} -> only {len(rows)} candles via {epic}"); return None
    df = pd.DataFrame(rows); df.index = pd.to_datetime(df["snapshotTime"])
    df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    df, _ = clean_spikes(df); df.to_pickle(p)
    tag = "" if epic == cand else f" (via {epic})"
    print(f"  {label:10} -> {len(df)} candles {df.index[0].date()}->{df.index[-1].date()}{tag}")
    return df


def main():
    client = CapitalClient.from_env(); client.login()
    print("Resolving + pulling Asian-session indices:")
    asian = {}
    for label, (cand, term) in ASIAN.items():
        df = pull(client, label, cand, term)
        if df is not None:
            asian[label] = df

    # correlation of each Asian index vs the Western index basket
    west = {}
    for e in WESTERN_IDX:
        p = os.path.join(CACHE, f"{e}.pkl")
        if os.path.exists(p):
            west[e] = pd.read_pickle(p)["close"].pct_change()
    west_basket = pd.DataFrame(west).dropna().mean(axis=1)
    print("\nCorrelation with Western index basket (low => good diversifier):")
    asia_cluster = {}
    for e, df in asian.items():
        r = df["close"].pct_change()
        cc = r.reindex(west_basket.index).corr(west_basket)
        asia_cluster[e] = "equity_asia" if cc < 0.55 else "equity"
        print(f"  {e:10} corr {cc:+.2f}  -> cluster '{asia_cluster[e]}'")

    # build the FULL universe: existing 23 (from cache) + Asian, with cluster map
    grp_of = {k: v[1] for k, v in WANT.items()}
    corr_of = {k: ("equity" if g in ("index", "stock") else g) for k, g in grp_of.items()}
    data, groups, corrs = {}, {}, {}
    for e in list(grp_of):
        p = os.path.join(CACHE, f"{e}.pkl")
        if os.path.exists(p):
            data[e] = pd.read_pickle(p); groups[e] = grp_of[e]; corrs[e] = corr_of[e]
    for e, df in asian.items():
        data[e] = df; groups[e] = "index"; corrs[e] = asia_cluster[e]

    def make_inst(keys):
        inst = {}
        for e in keys:
            px = float(data[e]["close"].median()); g = groups[e]
            sb = {"index": 0.0002, "metal": 0.0002, "energy": 0.0004, "ag": 0.0004}.get(g, 0.0003)
            inst[e] = Instrument(epic=e, group=g, value_per_point=1.0, min_size=0.01,
                                 size_step=0.01, spread_points=px * sb,
                                 financing_daily=px * 0.00008, corr_group=corrs[e])
        return inst

    base_keys = [e for e in data if e not in asian]
    full_keys = list(data)
    cfg = Config()
    rb = Backtest(cfg, make_inst(base_keys), {e: data[e] for e in base_keys}).run()["metrics"]
    rf = Backtest(cfg, make_inst(full_keys), {e: data[e] for e in full_keys}).run()
    mf = rf["metrics"]
    print(f"\nBASE  ({len(base_keys)} mkts): ret {rb['total_return_pct']}%  maxDD {rb['max_drawdown_pct']}%  "
          f"MAR {rb['mar_calmar']}  PF {rb['profit_factor']}  trades {rb['num_trades']}")
    print(f"+ASIA ({len(full_keys)} mkts): ret {mf['total_return_pct']}%  maxDD {mf['max_drawdown_pct']}%  "
          f"MAR {mf['mar_calmar']}  PF {mf['profit_factor']}  trades {mf['num_trades']}")
    pnl = collections.defaultdict(lambda: [0.0, 0])
    for t in rf["trades"]:
        if t["epic"] in asian:
            pnl[t["epic"]][0] += t["pnl"]; pnl[t["epic"]][1] += 1
    print("\nAsian-index contribution:")
    for e, v in sorted(pnl.items(), key=lambda kv: -kv[1][0]):
        print(f"  {e:10} {v[0]:>9,.0f}  ({v[1]} trades, cluster {corrs[e]})")

    # emit config universe block
    lines = ["universe:"]
    for e in full_keys:
        lines.append(f"  {{epic: {e}, group: {groups[e]}, corr: {corrs[e]}}}")
    with open("universe_block.yaml", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote universe_block.yaml ({len(full_keys)} markets) — ready to paste into config.yaml")

    build_dashboard(rf, "output/dashboard_capital.html",
                    title=f"Seykota Bot — {len(full_keys)}-market Capital.com backtest (with Asia · long-only · 3y)")
    daily_report(rf, md_path="output/eod_report_capital.md")


if __name__ == "__main__":
    main()
