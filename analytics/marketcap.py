"""Cached market-cap lookup (Yahoo, crumb workaround) — to exclude large/mega-caps from the
distribution_short feed. The edge works micro->mid and REVERSES on large-caps, and share price is
a bad proxy (ET trades at $19 but is a $67B large-cap; RAYA spiked to $455 but is a $4M micro).
Cached in DB table market_cap so the scheduler fetches each ticker once.
"""
import time
from datetime import datetime, timedelta

import requests

from export.database import get_connection

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_SESSION = _CRUMB = None


def _session():
    global _SESSION, _CRUMB
    if _SESSION is None:
        s = requests.Session(); s.headers.update(UA)
        try:
            s.get("https://fc.yahoo.com", timeout=10)
        except Exception:
            pass
        try:
            _CRUMB = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10).text
        except Exception:
            _CRUMB = None
        _SESSION = s
    return _SESSION, _CRUMB


def _fetch(t):
    s, crumb = _session()
    if not crumb:
        return None
    try:
        u = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}?modules=price&crumb={crumb}"
        j = s.get(u, timeout=12).json()
        return (((j.get("quoteSummary") or {}).get("result") or [{}])[0]
                .get("price") or {}).get("marketCap", {}).get("raw")
    except Exception:
        return None


def market_cap(ticker, max_age_days=30):
    """Market cap in USD (DB-cached, refreshed every `max_age_days`). None if unavailable."""
    t = ticker.upper()
    c = get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS market_cap (ticker TEXT PRIMARY KEY, cap REAL, fetched TEXT)")
    row = c.execute("SELECT cap, fetched FROM market_cap WHERE ticker=?", (t,)).fetchone()
    fresh = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    if row and row["fetched"] and row["fetched"] >= fresh:
        c.close()
        return row["cap"]
    cap = _fetch(t)
    time.sleep(0.05)
    if cap is not None:
        c.execute("INSERT OR REPLACE INTO market_cap VALUES (?,?,?)",
                  (t, cap, datetime.now().strftime("%Y-%m-%d")))
        c.commit()
    c.close()
    return cap


def is_micro_to_mid(ticker, max_cap=10e9):
    """True if micro->mid (<$10B) OR cap unknown (don't over-filter / break on a fetch miss);
    False only when we KNOW it's a large/mega-cap (where the edge reverses)."""
    cap = market_cap(ticker)
    return cap is None or cap < max_cap
