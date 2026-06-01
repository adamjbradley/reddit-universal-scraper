"""Independent ATTENTION via Wikipedia pageviews (free, no key, historical to 2015).

Reading behaviour is genuinely independent of Reddit. Two uses:
  - FEAR-TERM pages (Stock market crash / Recession / Bear market) = an independent macro-fear
    gauge to CROSS-VALIDATE the RRAI capitulation edge across regimes;
  - COMPANY pages = independent per-ticker attention to cross-confirm pump/attention signals.
Stored in external_sentiment(source='wikipedia', ticker, date, volume=views; tone=NULL).
Resumable (INSERT OR REPLACE). Reliable REST API (unlike StockTwits/GDELT-free).
"""
import time
import urllib.parse

import requests

from export.database import get_connection

API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
       "en.wikipedia/all-access/all-agents/{}/daily/{}/{}")
UA = {"User-Agent": "reddit-signal-research/1.0 (adam_j_bradley@yahoo.com)"}

# Macro fear gauge (independent of Reddit/markets).
FEAR_PAGES = {"fear_crash": "Stock_market_crash", "fear_recession": "Recession",
              "fear_bear": "Bear_market", "fear_correction": "Stock_market_downturn"}
# A few high-attention tickers (ticker -> Wikipedia article title).
TICKER_PAGES = {"NVDA": "Nvidia", "TSLA": "Tesla,_Inc.", "GME": "GameStop",
                "AMC": "AMC_Theatres", "AAPL": "Apple_Inc.",
                "PLTR": "Palantir_Technologies", "BTC": "Bitcoin"}


def _ensure():
    c = get_connection()
    c.execute("""CREATE TABLE IF NOT EXISTS external_sentiment(
                   source TEXT, ticker TEXT, date TEXT, tone REAL, volume REAL,
                   PRIMARY KEY(source, ticker, date))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_extsent ON external_sentiment(ticker, date)")
    c.commit()
    c.close()


def fetch_pageviews(article, start, end):
    """{YYYY-MM-DD: views} for a Wikipedia article between start/end (YYYYMMDD)."""
    url = API.format(urllib.parse.quote(article, safe=''), start, end)
    try:
        r = requests.get(url, headers=UA, timeout=45)
        if r.status_code != 200:
            return {}
        items = r.json().get("items", [])
    except Exception:
        return {}
    out = {}
    for it in items:
        ts = it["timestamp"]  # YYYYMMDD00
        out[f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"] = it.get("views")
    return out


def wikipedia_backfill(pages=None, start="20150701", end="20260602", pause=0.5):
    """Backfill daily pageviews for fear-terms + tickers. Returns rows written."""
    _ensure()
    pages = pages or {**FEAR_PAGES, **TICKER_PAGES}
    conn = get_connection()
    n = 0
    for key, article in pages.items():
        data = fetch_pageviews(article, start, end)
        rows = [(key, d, v) for d, v in data.items()]
        conn.executemany("""INSERT OR REPLACE INTO external_sentiment(source, ticker, date, tone, volume)
                            VALUES('wikipedia', ?, ?, NULL, ?)""", rows)
        conn.commit()
        print(f"   📖 wiki {key} ({article}): {len(rows)} days")
        n += len(rows)
        time.sleep(pause)
    conn.close()
    return n
