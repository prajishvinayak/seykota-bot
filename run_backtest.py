#!/usr/bin/env python3
"""
Run the Seykota-style backtest and produce a dashboard + EOD report.

Usage:
  python run_backtest.py                 # offline demo data (no network)
  python run_backtest.py --source yfinance --years 3
  python run_backtest.py --source capital --years 3   # needs .env with API creds

Outputs (in ./output):
  results.json, dashboard.html, eod_report.md
"""
from __future__ import annotations
import argparse, json, os
from seykota_bot.engine import Config, Backtest
from seykota_bot.data import synthetic_universe, load_yfinance, YF_TICKERS
from seykota_bot.dashboard import build_dashboard
from seykota_bot.eod_report import daily_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="synthetic",
                    choices=["synthetic", "yfinance", "capital"])
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--risk", type=float, default=0.01)
    ap.add_argument("--heat", type=float, default=0.15)
    ap.add_argument("--outdir", default="output")
    args = ap.parse_args()

    cfg = Config(risk_pct=args.risk, heat_cap=args.heat)

    if args.source == "synthetic":
        instruments, data = synthetic_universe(years=args.years)
        title = f"Seykota Bot — Backtest (DEMO synthetic {args.years}y)"
    elif args.source == "yfinance":
        from seykota_bot.data import synthetic_universe as su
        instruments, _ = su(years=args.years)          # reuse instrument specs
        data = load_yfinance(list(YF_TICKERS.keys()), years=args.years)
        instruments = {k: v for k, v in instruments.items() if k in data}
        title = f"Seykota Bot — Backtest (Yahoo {args.years}y REAL data)"
    else:  # capital
        from seykota_bot.capital_client import CapitalClient
        from seykota_bot.data import synthetic_universe as su, load_capital
        instruments, _ = su(years=args.years)
        client = CapitalClient.from_env()
        client.login()
        data = load_capital(client, list(instruments.keys()), years=args.years)
        instruments = {k: v for k, v in instruments.items() if k in data}
        title = f"Seykota Bot — Backtest (Capital.com {args.years}y REAL data)"

    bt = Backtest(cfg, instruments, data)
    results = bt.run()

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    build_dashboard(results, os.path.join(args.outdir, "dashboard.html"), title=title)
    daily_report(results, md_path=os.path.join(args.outdir, "eod_report.md"))

    m = results["metrics"]
    print(f"\n{title}")
    print("-" * 60)
    for k in ["start_equity", "end_equity", "total_return_pct", "cagr_pct",
              "max_drawdown_pct", "sharpe", "sortino", "mar_calmar",
              "num_trades", "win_rate_pct", "profit_factor", "payoff_ratio",
              "expectancy_per_trade"]:
        print(f"  {k:>22}: {m.get(k)}")
    print(f"\nWrote: {args.outdir}/dashboard.html, results.json, eod_report.md")


if __name__ == "__main__":
    main()
