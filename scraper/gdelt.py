"""Independent NEWS sentiment via GDELT (free, no key, historical to 2017).

GDELT's `tone` = mean article sentiment for a query; `volume` = coverage intensity. This is a
fully INDEPENDENT source (global news, not social) - so it can CROSS-CONFIRM the Reddit RRAI
signal: if retail capitulates AND news tone bottoms, that's two independent reads agreeing.
Stored in external_sentiment(source='gdelt', ...). Resumable (INSERT OR REPLACE).
"""
import time
import datetime
import requests

from export.database import get_connection

DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
UA = {"User-Agent": "Mozilla/5.0 (compatible; reddit-scraper/1.0)"}


def _ensure():
    c = get_connection()
    c.execute("""CREATE TABLE IF NOT EXISTS external_sentiment(
                   source TEXT, ticker TEXT, date TEXT, tone REAL, volume REAL,
                   PRIMARY KEY(source, ticker, date))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_extsent ON external_sentiment(ticker, date)")
    c.commit()
    c.close()


def fetch_tone_recent(query, timespan="1w"):
    """Recent daily news tone + volume for `query` via the reliable `timespan` mode (the free
    GDELT DOC API works for recent windows; deep historical date-ranges are blocked - that
    needs GDELT BigQuery). Returns {YYYY-MM-DD: [tone, volume]}."""
    out = {}
    for mode, idx in (("timelinetone", 0), ("timelinevol", 1)):
        try:
            r = requests.get(DOC, params={"query": query, "mode": mode, "format": "json",
                                          "timespan": timespan, "timelinesmooth": "0"},
                             headers=UA, timeout=45)
            tl = r.json().get("timeline", [])
            if tl:
                for pt in tl[0].get("data", []):
                    d = pt["date"][:8]
                    iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                    out.setdefault(iso, [None, None])[idx] = pt.get("value")
        except Exception:
            pass
        time.sleep(1.0)
    return out


# Default queries: market-level (cross-confirm the RRAI macro signal) + a few mega-caps.
DEFAULT_QUERIES = {"MARKET": "stock market", "NVDA": "Nvidia", "TSLA": "Tesla",
                   "AAPL": "Apple stock", "SPY": "S&P 500"}


def gdelt_poll(queries=None, timespan="1w"):
    """FORWARD collection: fetch recent news tone/volume for each query and store daily rows.
    Run periodically (scheduler) to accumulate an independent news-sentiment series that
    cross-confirms the Reddit signals. Returns rows written."""
    _ensure()
    queries = queries or DEFAULT_QUERIES
    conn = get_connection()
    n = 0
    for ticker, q in queries.items():
        for iso, (tone, vol) in fetch_tone_recent(q, timespan).items():
            conn.execute("""INSERT OR REPLACE INTO external_sentiment(source, ticker, date, tone, volume)
                            VALUES('gdelt', ?, ?, ?, ?)""", (ticker, iso, tone, vol))
            n += 1
    conn.commit()
    conn.close()
    return n
