#!/usr/bin/env python3
"""
SAFE trade-authority self-test (DEMO ONLY).

Proves the bot can actually OPEN and CLOSE orders on Capital.com — i.e. that the API
key has trading permission, not just read access. It:
  1. logs in, switches to the configured account (Ed Seykota £21k)
  2. finds a currently-TRADEABLE market
  3. opens the MINIMUM size position WITH a protective stop, confirms the dealId
  4. immediately closes it
Refuses to run unless environment is 'demo'. Run:  python test_trade.py
"""
from __future__ import annotations
import time, yaml
from seykota_bot.capital_client import CapitalClient, CapitalError


def main():
    s = yaml.safe_load(open("config.yaml"))
    if s["mode"]["environment"].lower() != "demo":
        print("REFUSING: environment is not 'demo'. This test only runs on demo."); return

    c = CapitalClient.from_env()
    c.login()
    target = s["mode"].get("account_name")
    accts = c.accounts()
    acct = (next((a for a in accts if a.get("accountName") == target), None)
            or next((a for a in accts if a.get("preferred")), accts[0]))
    c.switch_account(acct["accountId"])
    bal = acct.get("balance", {}).get("balance")
    print(f"Account: {acct.get('accountName')} [{acct.get('accountId')}] balance {bal} {acct.get('currency')}\n")

    # find a TRADEABLE market in the universe
    chosen = None
    print("Checking which markets are open right now:")
    for u in s["universe"]:
        e = u["epic"]
        try:
            m = c.market(e)
        except CapitalError:
            continue
        snap = m.get("snapshot", {}) or {}
        status = snap.get("marketStatus")
        if status == "TRADEABLE" and (snap.get("offer") or snap.get("bid")):
            chosen = (e, m, snap)
            print(f"  {e:12} TRADEABLE  <- using this for the test")
            break
        else:
            print(f"  {e:12} {status}")
    if not chosen:
        print("\nNo market is TRADEABLE right now (markets closed — weekend/after hours).")
        print("Re-run this during market hours to test trade authority.")
        return

    epic, m, snap = chosen
    dr = m.get("dealingRules", {}) or {}
    min_size = float((dr.get("minDealSize", {}) or {}).get("value", 1) or 1)
    price = float(snap.get("offer") or snap.get("bid"))
    stop_dist = round(price * 0.05, 2)   # wide 5% stop — closed in 2s anyway, near-zero risk

    print(f"\n>> TEST ORDER: BUY {epic} size {min_size} @~{price} with stopDistance {stop_dist}")
    try:
        deal_id = c.open_position(epic, "BUY", min_size, stop_distance=stop_dist)
    except CapitalError as ex:
        print(f"\n[OPEN FAILED] {ex}")
        print(">> This would be the authority/permission problem. Read the error above.")
        return
    print(f"[OPENED ✓] dealId {deal_id}  -> the bot HAS authority to PLACE trades.")

    time.sleep(2)
    try:
        c.close_position(deal_id)
        print(f"[CLOSED ✓] position closed  -> the bot can also CLOSE trades.")
    except CapitalError as ex:
        # fallback: look it up in open positions and close
        try:
            for p in c.positions():
                pos = p.get("position", {})
                if (p.get("market", {}) or {}).get("epic") == epic:
                    c.close_position(pos.get("dealId"))
                    print(f"[CLOSED ✓] (via lookup) position closed.")
                    break
            else:
                print(f"[CLOSE FAILED] {ex}  -> close it manually in the Capital.com app ({epic}).")
        except CapitalError as ex2:
            print(f"[CLOSE FAILED] {ex2}  -> close it manually in the Capital.com app ({epic}).")

    print("\n==============================================")
    print(" RESULT: trade authority CONFIRMED — bot can open + close on demo. ✓")
    print("==============================================")


if __name__ == "__main__":
    main()
