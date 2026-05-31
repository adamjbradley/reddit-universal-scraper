"""EOD price feed (Yahoo Finance chart API, free, no key) for forward-return / author
hit-rate validation.

Closes are cached in the prices table; forward_return() measures how a ticker moved in
the N trading days after a mention - the ground truth that validates credibility scores.
"""
import time
import bisect
from datetime import datetime, timezone

import requests

from export.database import get_connection

_UA = {"User-Agent": "Mozilla/5.0 (compatible; reddit-scraper/1.0)"}


def fetch_prices(ticker, rng="2y"):
    """Fetch daily closes from the Yahoo chart API into the prices table. Returns count."""
    t = ticker.upper()
    conn = get_connection()
    cur = conn.cursor()
    n = 0
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={rng}&interval=1d"
        r = requests.get(url, headers=_UA, timeout=20)
        res = (r.json().get("chart") or {}).get("result")
        if res:
            data = res[0]
            ts = data.get("timestamp") or []
            quote = (data.get("indicators") or {}).get("quote") or [{}]
            closes = quote[0].get("close") or []
            for epoch, close in zip(ts, closes):
                if close is None:
                    continue
                d = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
                cur.execute("INSERT OR REPLACE INTO prices(ticker, date, close) VALUES(?,?,?)",
                            (t, d, float(close)))
                n += 1
    except Exception:
        pass
    cur.execute("INSERT OR REPLACE INTO price_meta(ticker, fetched_at, ok) VALUES(?,?,?)",
                (t, datetime.now().isoformat(), 1 if n else 0))
    conn.commit()
    conn.close()
    return n


def get_closes(ticker):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT date, close FROM prices WHERE ticker=? ORDER BY date", (ticker.upper(),))
    res = cur.fetchall()
    conn.close()
    return [(r["date"], r["close"]) for r in res]


def forward_return(ticker, date_iso, horizon=5):
    """Return from the first close on/after the mention date to `horizon` sessions later."""
    closes = get_closes(ticker)
    if not closes:
        return None
    dates = [c[0] for c in closes]
    i = bisect.bisect_left(dates, date_iso[:10])
    if i >= len(closes) or i + horizon >= len(closes):
        return None
    p0, p1 = closes[i][1], closes[i + horizon][1]
    if p0 <= 0:
        return None
    return (p1 / p0) - 1


def price_backfill(tickers, force=False, pause=0.4):
    """Fetch prices for tickers not yet attempted (or all, if force)."""
    conn = get_connection()
    cur = conn.cursor()
    done = set() if force else {r["ticker"] for r in cur.execute("SELECT ticker FROM price_meta")}
    conn.close()
    fetched = ok = 0
    for t in tickers:
        if t.upper() in done:
            continue
        if fetch_prices(t) > 0:
            ok += 1
        fetched += 1
        time.sleep(pause)
    return {"attempted": fetched, "with_data": ok}


def tracked_tickers(min_mentions=3):
    """Distinct tickers worth fetching prices for (mentioned >= min_mentions)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT ticker FROM ticker_mentions GROUP BY ticker
                   HAVING COUNT(*) >= ? ORDER BY COUNT(*) DESC""", (min_mentions,))
    res = [r["ticker"] for r in cur.fetchall()]
    conn.close()
    return res
