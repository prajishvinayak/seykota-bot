"""
Data loaders.

- synthetic_universe(): offline, realistic trending OHLC for demo (no network).
- load_yfinance(): real daily data via Yahoo (run locally; needs `pip install yfinance`).
- load_capital(): real data via Capital.com /prices (run locally with your API key).

All loaders return: dict[epic] -> DataFrame indexed by datetime with
columns: open, high, low, close, volume.
"""
from __future__ import annotations
from typing import Dict, List
import numpy as np
import pandas as pd
from .engine import Instrument


def clean_spikes(df: pd.DataFrame, rev_thresh: float = 0.30):
    """
    Repair single-bar bad ticks (e.g. Capital demo's NVDA 2024-06-10 = 12.16 vs ~121
    neighbours). A bar is a spike if its close deviates > rev_thresh from BOTH the
    prior and next close in the SAME direction (i.e. it jumps then immediately reverts).
    Such bars have their OHLC replaced by the neighbour average. Returns (df, n_fixed).
    Threshold 30% is far above any legitimate daily move for these instruments.
    """
    if len(df) < 3:
        return df, 0
    df = df.copy()
    c = df["close"].to_numpy(dtype=float)
    fixed = 0
    for i in range(1, len(c) - 1):
        prev, cur, nxt = c[i - 1], c[i], c[i + 1]
        if prev <= 0 or nxt <= 0 or cur <= 0:
            continue
        d_prev, d_next = cur / prev - 1.0, cur / nxt - 1.0
        if abs(d_prev) > rev_thresh and abs(d_next) > rev_thresh and d_prev * d_next > 0:
            for col in ("open", "high", "low", "close"):
                df.iat[i, df.columns.get_loc(col)] = (df[col].iat[i - 1] + df[col].iat[i + 1]) / 2.0
            fixed += 1
    return df, fixed


# --------------------------------------------------------------------------- #
# Offline synthetic data (for the demo run in a no-network sandbox)
# --------------------------------------------------------------------------- #
def _synth_series(seed: int, n: int, start_price: float, ann_vol: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # regime-switching drift so real trends exist for a trend-follower to catch,
    # interspersed with choppy/range-bound regimes that produce false breakouts
    # (whipsaws) -> realistic low win rate, meaningful drawdowns.
    daily_vol = ann_vol / np.sqrt(252)
    # Regime drift gives multi-month trends; a gentle mean-reversion (OU) pull
    # keeps prices in a realistic band (no 10x random walks) and creates the
    # false breakouts / whipsaws that make win rate realistic (~40%).
    drift = np.zeros(n)
    i = 0
    while i < n:
        length = int(rng.integers(20, 90))
        if rng.random() < 0.6:                       # mostly choppy
            mu = rng.normal(0, 0.06) * daily_vol
        else:                                        # occasional trend
            mu = rng.normal(0, 0.30) * daily_vol
        drift[i:i + length] = mu
        i += length
    shocks = rng.standard_t(5, n) * daily_vol * 0.9  # fat tails
    theta = 0.02                                     # mean-reversion strength (binds)
    band = 0.45                                      # max ~+/-45% excursion in log space
    logp = np.empty(n)
    lp = np.log(start_price)
    anchor = np.log(start_price)
    for t in range(n):
        lp = lp + drift[t] + shocks[t] - theta * (lp - anchor)
        # keep within a realistic band around the anchor (no 10x walks)
        lp = anchor + np.clip(lp - anchor, -band, band)
        logp[t] = lp
    close = np.exp(logp)
    # build OHLC around close
    intraday = np.abs(rng.normal(0, daily_vol, n)) * close
    high = close + intraday * rng.uniform(0.3, 1.0, n)
    low = close - intraday * rng.uniform(0.3, 1.0, n)
    open_ = np.concatenate([[start_price], close[:-1]]) + rng.normal(0, daily_vol * 0.3, n) * close
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(1000, 5000, n)},
        index=dates,
    )


def synthetic_universe(years: int = 3):
    """Return (instruments, data) for an 8-instrument demo over ~`years` years."""
    n = int(years * 252)
    specs = [
        # epic,        group,        seed, start, ann_vol, value_per_point, min, step
        ("US500",      "equity_idx",  1, 4200.0, 0.16, 1.0, 0.1, 0.1),
        ("US100",      "equity_idx",  2, 14500.0, 0.20, 1.0, 0.1, 0.1),
        ("DE40",       "equity_idx",  3, 16000.0, 0.18, 1.0, 0.1, 0.1),
        ("GOLD",       "metals",      4, 1950.0, 0.14, 1.0, 0.1, 0.1),
        ("SILVER",     "metals",      5, 24.0,   0.28, 50.0, 0.5, 0.5),
        ("OIL_CRUDE",  "energy",      6, 78.0,   0.35, 10.0, 0.5, 0.5),
        ("NATURALGAS", "energy",      7, 2.8,    0.55, 100.0, 1.0, 1.0),
        ("COPPER",     "metals",      8, 3.8,    0.24, 100.0, 1.0, 1.0),
    ]
    instruments: Dict[str, Instrument] = {}
    data: Dict[str, pd.DataFrame] = {}
    for epic, group, seed, px, vol, vpp, mn, step in specs:
        instruments[epic] = Instrument(
            epic=epic, group=group, value_per_point=vpp,
            min_size=mn, size_step=step,
            spread_points=px * 0.0002,            # ~2bp half-spread
            financing_daily=px * vpp * 0.00008,   # tiny overnight cost
        )
        data[epic] = _synth_series(seed, n, px, vol)
    return instruments, data


# --------------------------------------------------------------------------- #
# Real data: Yahoo Finance (run locally)
# --------------------------------------------------------------------------- #
YF_TICKERS = {
    "US500": "ES=F", "US100": "NQ=F", "DE40": "^GDAXI",
    "GOLD": "GC=F", "SILVER": "SI=F", "OIL_CRUDE": "CL=F",
    "NATURALGAS": "NG=F", "COPPER": "HG=F",
}


def load_yfinance(epics: List[str], years: int = 3, ticker_map: dict | None = None):
    """Real daily OHLC via yfinance. Requires: pip install yfinance"""
    import yfinance as yf
    tmap = ticker_map or YF_TICKERS
    end = pd.Timestamp.today()
    start = end - pd.DateOffset(years=years)
    data = {}
    for epic in epics:
        tkr = tmap.get(epic, epic)
        df = yf.download(tkr, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            print(f"  [yfinance] no data for {epic} ({tkr})")
            continue
        # Recent yfinance returns a MultiIndex (field, ticker) even for one ticker.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        try:
            df = df[["open", "high", "low", "close", "volume"]]
        except KeyError:
            print(f"  [yfinance] unexpected columns for {epic} ({tkr}): {list(df.columns)}")
            continue
        df.index = pd.to_datetime(df.index)
        df = df.apply(pd.to_numeric, errors="coerce").dropna()
        if len(df) < 200:
            print(f"  [yfinance] only {len(df)} bars for {epic} ({tkr}) — skipping (need >=200)")
            continue
        print(f"  [yfinance] {epic} ({tkr}): {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}")
        data[epic] = df
    return data


# --------------------------------------------------------------------------- #
# Real data: Capital.com /prices (run locally with your API key)
# --------------------------------------------------------------------------- #
def load_capital(client, epics: List[str], years: int = 3, resolution: str = "DAY"):
    """
    Pull historical candles from Capital.com.
    `client` is a CapitalClient (see capital_client.py) with an open session.
    Capital.com caps candles per request, so we page backwards in chunks.
    """
    end = pd.Timestamp.utcnow()
    start = end - pd.DateOffset(years=years)
    data = {}
    for epic in epics:
        rows = client.price_history(epic, resolution=resolution,
                                    start=start.isoformat(), end=end.isoformat())
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["snapshotTime"])
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        data[epic] = df.dropna()
    return data
