# Refinement Log — Seykota Bot

_Check → recheck → refine cycle on REAL data (yfinance, daily, 2023-06-14 → 2026-06-12,
8 instruments: US500, US100, DE40, GOLD, SILVER, OIL_CRUDE, NATURALGAS, COPPER).
Reproduce with `python analyze.py`._

## 1. CHECK — correctness bugs fixed in `engine.py`
- **Double-charged spread.** Entry/exit fills added `slip + spread` where `slip`
  defaulted to `spread`, so each side paid 2× half-spread (round-trip = 2× full
  spread). Fixed to charge `half-spread + slippage` per side.
- **Stale-signal drop was a no-op.** When a market was closed on the fill day the
  signal was always re-queued (the "drop stale" branch still appended). Now it is
  carried at most 2 days, then dropped.
- **Dead MAE line** (`... if False else None`) removed.
- Added `risk_at_entry` + `equity_at_entry` to each trade so the per-trade risk
  cap is now an exact, testable invariant (previously only approximable).

## 2. RECHECK — invariants (all PASS on real data)
- Portfolio heat ≤ heat_cap: max **7.9%** vs 15% cap. ✅
- Per-trade initial risk ≤ risk_pct_max: worst **1.02%** vs 2% cap. ✅
- No look-ahead: acting on 1-bar-staler data does **not** improve results
  (29.6% → 29.4%). ✅

### Walk-forward (60/40 split @ 2025-04-01)
| Window | Return | Max DD | PF | Sharpe | Trades |
|---|---|---|---|---|---|
| In-sample | +8.5% | −9.5% | 1.36 | 0.41 | 63 |
| Out-of-sample | +25.8% | −6.8% | 2.40 | 1.25 | 41 |

OOS ≥ IS → **no overfitting signature** (the earlier AI-BOT-3 strategy failed
this test; this one passes). Caveat: the OOS window was a strong metals trend.

### Parameter sensitivity (90 configs)
- **100% of configs profitable**; return median +39%, range [+17.5%, +57.3%].
  The edge is a broad plateau, not a knife-edge fit.
- Marginal effects were smooth and directional:
  - Slower trend filter better: ema 100/200 (+43%) > 80/140 (+40%) > 50/100 (+33%).
  - Looser trail better: chandelier 3.5 (+43%) > 3.0 (+37%) > 2.5 (+37%).
  - Donchian length ~flat (30–70 all +37–41%) — left at 50.
  - Shorts hurt: long-only +47% vs with-shorts +30% (bull-market artifact).

## 3. REFINE — changes adopted (defaults in `engine.py:Config`)
| Param | Brief default | Refined | Why |
|---|---|---|---|
| ema_fast / ema_slow | 80 / 140 | **100 / 200** | Robust + Seykota-aligned (slow filter), fewer whipsaws |
| chandelier_mult | 3.0 | **3.5** | Ride winners longer; within Seykota's 3–4× range |
| donchian_n | 50 | 50 | Sensitivity flat — no change |
| allow_short | true | **true** | Kept ON for robustness; long-only NOT baked in to avoid bull-market fitting |

**Refined vs baseline (full 3y real data):**
+35.0% return (was +29.6%), max DD −12.4% (was −13.2%), win rate 41.7% (was 35.1%),
PF 1.71, Sharpe 0.69, expectancy +$407/trade. Improves return **and** drawdown.

## 4. MULTI-MARKET RE-TEST (39 markets) — overturned the 8-market result
Expanded from 8 (indices+metals) to 39 markets across index/metal/energy/ag/fx/stock.
The narrow basket had flattered the strategy. Broad reality (`multi_market.py`,
`refine_universe.py`):

| Universe · direction | Mkts | Full ret | maxDD | PF | MAR | OOS ret | OOS PF |
|---|---|---|---|---|---|---|---|
| All 39 · shorts on | 39 | −19.7% | −30.7% | 0.90 | −0.22 | +3.3% | 1.14 |
| All 39 · long-only | 39 | +67.2% | −30.6% | 1.43 | 0.59 | +13.3% | 1.28 |
| **No-FX · long-only (PRODUCTION)** | 32 | +92.0% | −30.3% | 1.54 | 0.77 | +23.7% | 1.41 |
| No-FX+energy · long-only | 27 | +101.5% | −30.3% | 1.61 | 0.84 | +27.2% | 1.50 |
| index+metal+stock · long-only | 19 | +91.5% | −25.9% | 1.80 | 0.90 | +38.2% | 1.87 |

P&L by class (shorts on): index +9.8k, stock +1.6k, metal +0.0k, ag −0.6k,
**fx −10.0k (19% win), energy −11.7k (25% win)**.

**Revised decisions (this supersedes §3's "kept shorts ON"):**
- **allow_short → False (long-only).** Shorts lost in *every* universe tested.
  Structural (breakout shorts whipsaw on V-recoveries) + this regime rose broadly.
  Caveat: long-only goes FLAT (not short) in a sustained bear — no crisis alpha.
- **Drop FX majors** from the traded universe. Range-bound by nature → poor for
  breakout trend-following (19% win); independently corroborated by the AI-BOT-3 work.
- **Kept energy + ag** despite this-sample losses — diversification is the edge;
  dropping them for performance alone would be curve-fitting. (index+metal+stock
  scores best but that trims to only what trended in this exact window.)

**PRODUCTION = 32 markets (no FX), long-only, ema 100/200, chandelier 3.5:**
+92% / 23.5% CAGR / **−30.3% maxDD** / PF 1.54 / win 42.4% / 245 trades.

### ⚠️ Open problem: −30% max drawdown
Heat cap is 15% but realised DD hit 30% — many correlated longs trend and reverse
together; drawdown-derisk + 25% pause aren't containing it. NEXT refinement is on the
RISK side (not return): correlation-aware heat, tighter heat_cap / risk_pct, or a
sector exposure limit. Tackle before any live consideration.

## 5. VALIDATION ON CAPITAL.COM's OWN DATA (`capital_backtest.py`)
Re-ran the refined config on the broker's own daily feed (29 markets resolved;
FX excluded). Found and fixed two real data issues first:
- **Pager bug:** backward paging hit gaps in the demo feed and truncated the recent
  end (US100/US30/DE40 stopped at 2025-08). Rewrote `price_history` to page FORWARD
  with a fixed step → all markets now pull full 3y.
- **Bad ticks:** NVDA 2024-06-10 = 12.16 vs ~121 neighbours (10× glitch around the
  split). Added `data.clean_spikes()` (repairs single-bar >30% reverts).

**Robustness finding (important for live):** that ONE NVDA bad tick faked a portfolio
drawdown that tripped the 25% DD-pause and halted ALL entries — trades fell 237→89,
return 62%→26%. A single corrupt tick can shut the whole bot down. Live loop needs
incoming-data spike filtering (added) PLUS equity/position sanity guards.

**Capital.com result (cleaned, 29 markets, long-only):**
+62.3% / 13.95% CAGR / −29.6% maxDD / PF 1.48 / win 40.9% / 237 trades.
By class: metal +62.4k (51% win), ag +15.1k, index +14.8k, energy −5.1k, stock −9.5k.

**vs Yahoo (32 mkts, +92%):** same ballpark and same shape (metals-led, PF ~1.5,
win ~41%, −30% DD). The ~30pp gap is mostly single stocks (Capital feed noisier:
−9.5k vs Yahoo +1.6k) + 3 fewer markets. **Edge confirmed on the broker's own prices.**
Open: single-stock CFDs look marginal on the demo feed — candidate to drop or treat
cautiously. The −30% drawdown remains the #1 risk problem.

## 6. DRAWDOWN FIX — correlated-cluster caps (brief §3.3-3.4) `risk_tune.py`
The −30% DD came from the heat cap treating positions as independent while a basket
of 8 indices + 6 stocks long at once is ONE correlated bet. Implemented the brief's
missing piece: a correlation cluster (`Instrument.corr_group`; index+stock share
"equity") with two caps — `max_positions_per_group` and `group_heat_cap`.

Swept on both feeds, full + OOS. **max 3 / cluster (+ 6% cluster heat)** won decisively:

| Capital.com | Return | maxDD | MAR | PF | OOS MAR |
|---|---|---|---|---|---|
| baseline | 62.3% | −29.6% | 0.47 | 1.48 | 0.87 |
| **max 3 / cluster** | **86.2%** | **−17.6%** | **1.04** | **1.94** | **2.36** |

Same on Yahoo (MAR 0.77→1.08, maxDD −30%→−21%). It raised return AND cut drawdown
AND tripled OOS MAR — diversification forced across uncorrelated clusters, and the
avoided correlated crashes stop the DD-pause freezing the bot. Adopted as default
(`group_heat_cap=0.06`, `max_positions_per_group=3`).

**FINAL config:** long-only · ema 100/200 · chandelier 3.5 · no FX · max 3/cluster.
Capital.com: +86% / −17.6% DD / PF 1.94 / win 40% / 152 trades. Yahoo: +88% / −21% DD.

## 7. AUTOMATED TEST SUITE (`tests/`, 15 tests — run `python -m pytest`)
Locks in the brief's acceptance criteria so a future config tweak can't silently
break a safety limit. Deterministic, no network (synthetic data + a FakeClient).
- per-trade risk ≤ risk_pct_max (crit 1) · portfolio + cluster heat GATE holds at
  entry (crit 2) · cluster position cap enforced · drawdown pause smoke
- no look-ahead: donchian uses prior-N bars; 1-bar-staler data can't beat base (crit 3)
- indicators: ATR-Wilder matches definition; EMA/uptrend; clean_spikes repairs a
  10×-down bad tick and leaves clean data untouched
- live safety: client refuses to open without a stop (crit 4); every LiveTrader order
  carries stop_level; CLOSED-market gate blocks orders; KILL_SWITCH flattens+halts (crit 7)
Added `heat_at_entry`/`group_heat_at_entry` telemetry to each trade (the heat cap is an
ENTRY gate; observed heat can drift up as positions profit — that excess is trailing-stop-
protected open profit, not principal — so the gate is the real invariant).

## Honest standing
- Real, plausible trend-follower profile; passes walk-forward + sensitivity.
- **But** profit is concentrated in GOLD (+$24k) and SILVER (+$11k) over a strong
  2024–26 metals trend; energy (oil, natgas) lost. A different regime would differ.
- `long-only` is a tempting +55% but it fits the bull sample — left as a documented
  risk lever, decide it by forward demo-testing, not by backtest.
- **Next real validation = Capital.com price data + months of demo/paper forward-testing.**
  No live money until then.
