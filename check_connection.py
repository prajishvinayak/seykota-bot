#!/usr/bin/env python3
"""
Read-only Capital.com connection test. Places NO orders.

  1. logs in with the creds in .env
  2. lists your accounts + balances
  3. resolves a couple of universe epics and pulls a sample of daily candles

Run:  python check_connection.py
"""
from __future__ import annotations
from seykota_bot.capital_client import CapitalClient, CapitalError

# universe epics from the brief — we'll verify these resolve on your account
PROBE_EPICS = ["GOLD", "SILVER", "US500", "OIL_CRUDE"]


def main():
    try:
        client = CapitalClient.from_env()
    except CapitalError as e:
        print(f"[CONFIG] {e}")
        return

    print(f"Environment: {client.environment.upper()}  base={client.base}")
    try:
        client.login()
    except CapitalError as e:
        print(f"[LOGIN FAILED] {e}")
        print("  -> check CAPITAL_API_KEY / CAPITAL_IDENTIFIER (login email) / "
              "CAPITAL_API_PASSWORD, and that the key is for this environment.")
        return
    print("[OK] Logged in — CST + security token received.\n")

    # accounts
    try:
        accts = client.accounts()
        print(f"Accounts ({len(accts)}):")
        for a in accts:
            print(f"  - {a.get('accountName')} [{a.get('accountId')}] "
                  f"{a.get('currency')}  balance={a.get('balance', {}).get('balance')}  "
                  f"available={a.get('balance', {}).get('available')}  "
                  f"{'(PREFERRED)' if a.get('preferred') else ''}")
    except CapitalError as e:
        print(f"[accounts] {e}")
    print()

    # epics + sample candles
    print("Probing universe epics + daily candles:")
    for epic in PROBE_EPICS:
        try:
            mkt = client.market(epic)
            snap = mkt.get("snapshot", {})
            inst = mkt.get("instrument", {})
            status = snap.get("marketStatus")
            rows = client.price_history(epic, resolution="DAY", max=10)
            last = rows[-1] if rows else {}
            print(f"  {epic:10} status={status:<10} "
                  f"minSize={inst.get('minDealSize') or inst.get('lotSize')}  "
                  f"candles={len(rows)}  last={last.get('snapshotTime','?')} "
                  f"close={last.get('close')}")
        except CapitalError as e:
            print(f"  {epic:10} [unavailable] {str(e)[:120]}")
            # try a search to suggest the right epic name
            for m in client.search_markets(epic.replace('_', ' '))[:3]:
                print(f"             ? did you mean: {m.get('epic')} ({m.get('instrumentName')})")

    print("\nDone. No orders were placed (read-only test).")


if __name__ == "__main__":
    main()
