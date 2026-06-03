"""Cached market-cap + SECTOR lookup (Yahoo, crumb workaround) — to filter the distribution_short
feed to the genuinely-tradeable, genuinely-works subset.

Two filters fall out of the cap-stratified and sector-stratified backtests:
  CAP    — edge works micro->small (<$2B), fades at mid, REVERSES at mega; share price is a bad
           proxy (ET is $19 but $67B). Keep <$10B (is_micro_to_mid).
  SECTOR — Energy is a squeeze LANDMINE (-20.6%, win 0%); Healthcare/biotech is ~0 (binary FDA
           catalysts, not distribution); Technology + Industrials are the core edge (+10-15%).

Both cached in DB table market_cap so the scheduler fetches each ticker once.
"""
import time
from datetime import datetime, timedelta

import requests

from export.database import get_connection

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_SESSION = _CRUMB = None

AVOID_SECTORS = {"Energy"}                       # squeeze landmine -> exclude from the feed
PREFERRED_SECTORS = {"Technology", "Industrials"}  # the core edge
WEAK_SECTORS = {"Healthcare"}                    # biotech: long-catalyst, not a short -> flag


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
    """(market_cap, sector) from one quoteSummary call."""
    s, crumb = _session()
    if not crumb:
        return None, None
    try:
        u = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{t}"
             f"?modules=price,assetProfile&crumb={crumb}")
        res = ((s.get(u, timeout=12).json().get("quoteSummary") or {}).get("result") or [{}])[0]
        cap = ((res.get("price") or {}).get("marketCap") or {}).get("raw")
        sec = (res.get("assetProfile") or {}).get("sector")
        return cap, sec
    except Exception:
        return None, None


def _lookup(ticker, max_age_days=30):
    """(cap, sector), DB-cached, refreshed every max_age_days."""
    t = ticker.upper()
    c = get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS market_cap (ticker TEXT PRIMARY KEY, cap REAL, sector TEXT, fetched TEXT)")
    if "sector" not in [r[1] for r in c.execute("PRAGMA table_info(market_cap)").fetchall()]:
        c.execute("ALTER TABLE market_cap ADD COLUMN sector TEXT")     # migrate older cache
    row = c.execute("SELECT cap, sector, fetched FROM market_cap WHERE ticker=?", (t,)).fetchone()
    fresh = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    if row and row["fetched"] and row["fetched"] >= fresh and row["sector"] is not None:
        c.close()
        return row["cap"], row["sector"]
    cap, sec = _fetch(t)
    time.sleep(0.05)
    if cap is not None or sec is not None:
        c.execute("INSERT OR REPLACE INTO market_cap (ticker, cap, sector, fetched) VALUES (?,?,?,?)",
                  (t, cap, sec, datetime.now().strftime("%Y-%m-%d")))
        c.commit()
    c.close()
    return cap, sec


def market_cap(ticker):
    return _lookup(ticker)[0]


def sector(ticker):
    return _lookup(ticker)[1]


def is_micro_to_mid(ticker, max_cap=10e9):
    """True if micro->mid (<$10B) OR cap unknown (don't over-filter); False only when KNOWN large/mega."""
    cap = market_cap(ticker)
    return cap is None or cap < max_cap


def sector_class(ticker):
    """'AVOID' (Energy, exclude) | 'PREFERRED' (Tech/Industrials) | 'WEAK' (Healthcare) | 'OK' | '?'."""
    sec = sector(ticker)
    if sec is None:
        return "?"
    if sec in AVOID_SECTORS:
        return "AVOID"
    if sec in PREFERRED_SECTORS:
        return "PREFERRED"
    if sec in WEAK_SECTORS:
        return "WEAK"
    return "OK"
