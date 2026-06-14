# How to run the Seykota Bot (paper / demo)

Everything is **demo** (fake money) until you deliberately change `config.yaml`.
Every order carries a **server-side stop**, and all risk caps are enforced live.

---

## 0. One-time setup (already done, just verify)
```powershell
cd "C:\Users\AddyP\Claude\Projects\Ed Seykota Bot"
python -m pip install -r requirements.txt
python check_connection.py        # should say "Logged in" and list your accounts
```
Your demo creds are already in `.env`. The bot uses the **preferred** account ("Ed Seykota", ~£21k).

---

## 1. ALWAYS dry-run first (no orders)
```powershell
python run_paper.py --dry-run
```
This logs exactly what it *would* do and writes `output\eod_report_live.md`. No trades.

---

## 2. Start it this morning — pick ONE option

### Option A — leave it running (simplest)
```powershell
python run_paper.py --loop
```
It runs one cycle **every day at 15:00 UTC** (when US + Europe + commodities are all open),
then sleeps. Just leave the window open. Ctrl+C to stop. If the PC sleeps/reboots it stops —
use Option B for unattended reliability.

### Option B — Windows Task Scheduler (robust, survives reboots)
1. Open **Task Scheduler** → **Create Basic Task** → name it "Seykota Bot".
2. Trigger: **Daily**, start time **16:00** (your local; = 15:00 UTC in winter / adjust for BST).
3. Action: **Start a program**
   - Program: `python`
   - Arguments: `run_paper.py --once`
   - Start in: `C:\Users\AddyP\Claude\Projects\Ed Seykota Bot`
4. Finish. It now runs one cycle daily, unattended.

### Option C — run a cycle by hand whenever
```powershell
python run_paper.py --once
```

> To kick things off **right now this morning**, just run Option C once. Then set up A or B
> for the daily routine.

---

## 3. The daily loop with Claude (no Claude credits used by the bot)
1. Bot runs its cycle → writes **`output\eod_report_live.md`**.
2. **Open that file, copy everything, paste it to Claude here.**
3. Claude reviews the trades/losers and suggests tweaks to **`config.yaml`** only.
4. You edit `config.yaml`; changes take effect next cycle. (Never edit code mid-run.)

The bot itself makes **zero** Claude/AI calls — running it costs you nothing in credits.

---

## 4. Emergency stop / kill switch
- **Flatten everything now:** `python run_paper.py --flatten`
- **Halt + flatten on every future cycle:** create an empty file named `KILL_SWITCH` in this folder.
  (Delete it to resume.)

---

## 5. What to watch in the first week (IMPORTANT)
The bot sizes positions assuming **1 unit = 1 currency-point of P&L**. Capital's real
contract sizes and the USD→GBP conversion mean the *actual* risk per trade may differ from
the intended 0.5%. **So for the first few trades, check the EOD report:** does each open
position's risk (size × distance-to-stop) ≈ **£105 (0.5% of £21k)**? If a market is sizing
much bigger/smaller, tell Claude the instrument + numbers and we'll set a per-instrument
`value_per_point`. This is exactly what the daily review is for.

Risk is intentionally **conservative (0.5%/trade)** to start; raise `risk_pct` toward the
backtested 0.01 only once sizing is confirmed correct.

---

## 6. Files
| File | What |
|---|---|
| `config.yaml` | all settings — the only thing you edit |
| `output/eod_report_live.md` | the daily report you paste to Claude |
| `output/dashboard_capital.html` | backtest dashboard (broker data) |
| `state/paper_state.json` | peak equity, day P&L, trailing-stop memory |
| `state/journal.jsonl` | append-only log of every order |
| `REFINEMENT_LOG.md` | full record of how the strategy was built & validated |
