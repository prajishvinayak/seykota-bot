"""
Capital.com REST client (read + trade).

Auth: POST /session with header X-CAP-API-KEY + body {identifier, password}.
The response headers carry CST and X-SECURITY-TOKEN, which must be sent on every
subsequent request. Sessions expire after ~10 min idle -> keepalive / re-login on 401.

Only the read methods (login, accounts, market, price_history) are needed for the
backtest. The order methods are here for the paper/live loop and ALWAYS attach a
protective stop. Nothing here moves real money on its own.

Docs: https://open-api.capital.com/
"""
from __future__ import annotations
import os
import time
import threading
from typing import Optional, List, Dict

import requests
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


API_PREFIX = "/api/v1"


class CapitalError(RuntimeError):
    pass


class _RateLimiter:
    """Token-ish limiter: enforce a minimum gap between requests (<=1 req / 0.1s)."""
    def __init__(self, min_interval: float = 0.12):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()


class CapitalClient:
    def __init__(self, api_key: str, identifier: str, password: str,
                 environment: str = "demo",
                 base_url_demo: str | None = None,
                 base_url_live: str | None = None):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.environment = environment.lower()
        demo = base_url_demo or "https://demo-api-capital.backend-capital.com"
        live = base_url_live or "https://api-capital.backend-capital.com"
        self.base = (demo if self.environment == "demo" else live).rstrip("/")
        self.cst: Optional[str] = None
        self.security_token: Optional[str] = None
        self._rl = _RateLimiter()
        self._session = requests.Session()

    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(cls) -> "CapitalClient":
        api_key = os.getenv("CAPITAL_API_KEY", "").strip()
        identifier = os.getenv("CAPITAL_IDENTIFIER", "").strip()
        password = os.getenv("CAPITAL_API_PASSWORD", "").strip()
        env = os.getenv("CAPITAL_ENVIRONMENT", "demo").strip().lower()
        missing = [k for k, v in {
            "CAPITAL_API_KEY": api_key,
            "CAPITAL_IDENTIFIER": identifier,
            "CAPITAL_API_PASSWORD": password,
        }.items() if not v]
        if missing:
            raise CapitalError(
                "Missing in .env: " + ", ".join(missing) +
                ".  (CAPITAL_IDENTIFIER is your login email; "
                "CAPITAL_API_PASSWORD is the password you set when creating the API key.)")
        return cls(api_key, identifier, password, environment=env,
                   base_url_demo=os.getenv("CAPITAL_BASE_URL_DEMO"),
                   base_url_live=os.getenv("CAPITAL_BASE_URL_LIVE"))

    # ------------------------------------------------------------------ #
    def _headers(self, auth: bool = True) -> dict:
        h = {"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"}
        if auth and self.cst and self.security_token:
            h["CST"] = self.cst
            h["X-SECURITY-TOKEN"] = self.security_token
        return h

    def _url(self, path: str) -> str:
        return f"{self.base}{API_PREFIX}{path}"

    def _request(self, method: str, path: str, *, auth: bool = True,
                 json_body: dict | None = None, params: dict | None = None,
                 _retry: bool = True) -> requests.Response:
        self._rl.wait()
        resp = self._session.request(
            method, self._url(path), headers=self._headers(auth),
            json=json_body, params=params, timeout=30)
        # session expired -> re-auth once and retry
        if resp.status_code == 401 and auth and _retry:
            self.login()
            return self._request(method, path, auth=auth, json_body=json_body,
                                 params=params, _retry=False)
        # rate limited -> back off once
        if resp.status_code == 429 and _retry:
            time.sleep(1.0)
            return self._request(method, path, auth=auth, json_body=json_body,
                                 params=params, _retry=False)
        return resp

    # ------------------------------------------------------------------ #
    def login(self) -> dict:
        """POST /session -> capture CST + X-SECURITY-TOKEN from response headers."""
        resp = self._request("POST", "/session", auth=False,
                             json_body={"identifier": self.identifier,
                                        "password": self.password})
        if resp.status_code != 200:
            raise CapitalError(f"Login failed [{resp.status_code}]: {resp.text[:300]}")
        self.cst = resp.headers.get("CST")
        self.security_token = resp.headers.get("X-SECURITY-TOKEN")
        if not (self.cst and self.security_token):
            raise CapitalError("Login returned no CST / X-SECURITY-TOKEN headers.")
        return resp.json()

    def keepalive(self) -> bool:
        resp = self._request("GET", "/session")
        if resp.status_code == 200:
            return True
        self.login()
        return False

    def accounts(self) -> list[dict]:
        resp = self._request("GET", "/accounts")
        if resp.status_code != 200:
            raise CapitalError(f"/accounts failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.json().get("accounts", [])

    def switch_account(self, account_id: str) -> bool:
        """PUT /session -> make `account_id` the active account for THIS session.
        Idempotent: 'already active' style responses are treated as success."""
        resp = self._request("PUT", "/session", json_body={"accountId": account_id})
        if resp.status_code in (200, 204):
            return True
        txt = resp.text.lower()
        if "same" in txt or "already" in txt or resp.status_code == 400:
            return True  # already on this account
        raise CapitalError(f"switch_account({account_id}) failed [{resp.status_code}]: {resp.text[:200]}")

    def market(self, epic: str) -> dict:
        resp = self._request("GET", f"/markets/{epic}")
        if resp.status_code != 200:
            raise CapitalError(f"/markets/{epic} failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.json()

    def search_markets(self, term: str) -> list[dict]:
        resp = self._request("GET", "/markets", params={"searchTerm": term})
        if resp.status_code != 200:
            return []
        return resp.json().get("markets", [])

    # ------------------------------------------------------------------ #
    @staticmethod
    def _mid(node: dict | None) -> float | None:
        if not node:
            return None
        bid, ask = node.get("bid"), node.get("ask")
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        return bid if bid is not None else ask

    def price_history(self, epic: str, resolution: str = "DAY",
                      start: str | None = None, end: str | None = None,
                      max: int = 1000) -> list[dict]:
        """
        Return [{snapshotTime, open, high, low, close, volume}, ...] as mid prices.
        Capital.com caps candles per request (~1000) so we page backwards by date.
        `start`/`end` are ISO strings; Capital expects 'YYYY-MM-DDTHH:MM:SS'.
        """
        def _naive(ts):
            ts = pd.Timestamp(ts)
            return ts.tz_localize(None) if ts.tz is not None else ts

        def _fmt(ts):
            return _naive(ts).strftime("%Y-%m-%dT%H:%M:%S")

        # Capital caps both candle count (<=1000) AND the from->to span per request,
        # so we page backwards in fixed windows sized to stay safely under the limit.
        WINDOW_DAYS = {"DAY": 300, "WEEK": 1500, "HOUR_4": 90, "HOUR": 30,
                       "MINUTE_30": 15, "MINUTE_15": 7, "MINUTE_5": 3, "MINUTE": 1}
        win = pd.Timedelta(days=WINDOW_DAYS.get(resolution, 300))

        now = _naive(pd.Timestamp.now("UTC"))
        end_ts = min(_naive(end), now) if end else now
        start_ts = _naive(start) if start else (end_ts - pd.DateOffset(years=3))
        out: list[dict] = []
        # page FORWARD with a fixed step: every sub-range is queried exactly once, so
        # gaps in the demo feed cannot truncate the recent end (a backward pager did).
        cursor = start_ts
        guard = 0
        last_err = None
        consecutive_fail = 0
        while cursor < end_ts and guard < 80:
            guard += 1
            cand = cursor + win
            win_end = end_ts if cand > end_ts else cand
            params = {"resolution": resolution, "max": max,
                      "from": _fmt(cursor), "to": _fmt(win_end)}
            resp = self._request("GET", f"/prices/{epic}", params=params)
            if resp.status_code != 200:
                # tolerate transient failures (e.g. error.invalid.daterange); keep paging
                last_err = f"[{resp.status_code}] {resp.text[:160]}"
                consecutive_fail += 1
                cursor = win_end + pd.Timedelta(days=1)
                if consecutive_fail >= 4:   # give up after several windows in a row
                    break
                continue
            consecutive_fail = 0
            for p in resp.json().get("prices", []):
                out.append({
                    "snapshotTime": p.get("snapshotTime") or p.get("snapshotTimeUTC"),
                    "open": self._mid(p.get("openPrice")),
                    "high": self._mid(p.get("highPrice")),
                    "low": self._mid(p.get("lowPrice")),
                    "close": self._mid(p.get("closePrice")),
                    "volume": p.get("lastTradedVolume", 0),
                })
            # advance regardless (handles gaps / empty windows)
            cursor = win_end + pd.Timedelta(days=1)
        if not out and last_err:
            raise CapitalError(f"/prices/{epic} failed: {last_err}")
        # dedupe + sort ascending
        seen, dedup = set(), []
        for r in sorted(out, key=lambda x: x["snapshotTime"] or ""):
            if r["snapshotTime"] in seen:
                continue
            seen.add(r["snapshotTime"])
            dedup.append(r)
        return dedup

    # ------------------------------------------------------------------ #
    # Trading (paper/live loop only) — ALWAYS sends a protective stop.
    # ------------------------------------------------------------------ #
    def open_position(self, epic: str, direction: str, size: float,
                      stop_distance: float | None = None,
                      stop_level: float | None = None,
                      trailing: bool = False, guaranteed: bool = False) -> str:
        if stop_distance is None and stop_level is None:
            raise CapitalError("Refusing to open a position without a protective stop.")
        body: dict = {"epic": epic, "direction": direction.upper(), "size": size,
                      "guaranteedStop": guaranteed, "trailingStop": trailing}
        if stop_level is not None:
            body["stopLevel"] = stop_level
        if stop_distance is not None:
            body["stopDistance"] = stop_distance
        resp = self._request("POST", "/positions", json_body=body)
        if resp.status_code not in (200, 201):
            raise CapitalError(f"open_position failed [{resp.status_code}]: {resp.text[:200]}")
        deal_ref = resp.json().get("dealReference")
        return self.confirm(deal_ref).get("dealId", deal_ref)

    def amend_position(self, deal_id: str, stop_level: float | None = None,
                       trailing: bool | None = None) -> dict:
        body = {}
        if stop_level is not None:
            body["stopLevel"] = stop_level
        if trailing is not None:
            body["trailingStop"] = trailing
        resp = self._request("PUT", f"/positions/{deal_id}", json_body=body)
        if resp.status_code != 200:
            raise CapitalError(f"amend_position failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.json()

    def close_position(self, deal_id: str) -> dict:
        resp = self._request("DELETE", f"/positions/{deal_id}")
        if resp.status_code != 200:
            raise CapitalError(f"close_position failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.json()

    def positions(self) -> list[dict]:
        resp = self._request("GET", "/positions")
        if resp.status_code != 200:
            raise CapitalError(f"/positions failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.json().get("positions", [])

    def confirm(self, deal_reference: str) -> dict:
        if not deal_reference:
            return {}
        resp = self._request("GET", f"/confirms/{deal_reference}")
        if resp.status_code != 200:
            return {"dealReference": deal_reference}
        return resp.json()
