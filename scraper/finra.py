"""FINRA short-selling pressure (Reg SHO daily short-volume files, free, no key, historical).

ShortVolume/TotalVolume = the % of a day's volume sold short. One HTTP request per trading day
covers ALL securities, so backfilling is cheap. Stored in short_volume(ticker, date, ...).

!! METHODOLOGY CAVEAT (validated 2026-06-02) !!
Reg SHO daily short-VOLUME is NOT short interest, and its ABSOLUTE level is NOT a squeeze signal.
A large fraction of "short volume" is bona-fide market-making: when an MM sells to a buyer before
sourcing the shares, it's booked as a short sale even though the MM is flat by EOD. As a result the
cross-ticker median short-ratio is ~0.49 and almost every liquid name sits in a 0.3-0.7 band -- the
high-absolute names are just low-liquidity mid-caps (restaurants/REITs), NOT squeezes.

The usable signal is the PER-TICKER Z-SCORE: today's short-ratio vs the ticker's OWN trailing
baseline. A jump well above a ticker's own mean = rising directional short pressure (e.g. GME
z=+1.50 while AMC z=-1.23 = shorts covering). `squeeze_screen()` uses the z-score, not the level.
For the true squeeze LEVEL (SI % of float, days-to-cover) we still need FINRA's bi-monthly
short-INTEREST report -- a separate, settlement-date dataset (see DATA_PLAN.md #3).
"""
import time
import datetime

import requests

from export.database import get_connection

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{}.txt"
UA = {"User-Agent": "reddit-signal-research/1.0 (adam_j_bradley@yahoo.com)"}


def _ensure():
    c = get_connection()
    c.execute("""CREATE TABLE IF NOT EXISTS short_volume(
                   ticker TEXT, date TEXT, short_volume REAL, total_volume REAL, short_ratio REAL,
                   PRIMARY KEY(ticker, date))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_shortvol ON short_volume(ticker, date)")
    c.commit()
    c.close()


def fetch_day(yyyymmdd, tickers=None):
    """Parse one Reg SHO daily file. Returns [(ticker, date, short_vol, total_vol, ratio)]."""
    try:
        r = requests.get(URL.format(yyyymmdd), headers=UA, timeout=30)
        if r.status_code != 200:
            return []
        lines = r.text.strip().split("\n")[1:]   # skip header
    except Exception:
        return []
    iso = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    rows = []
    for ln in lines:
        p = ln.split("|")
        if len(p) < 5:
            continue
        sym = p[1]
        if tickers is not None and sym not in tickers:
            continue
        try:
            sv, tv = float(p[2]), float(p[4])
        except ValueError:
            continue
        if tv > 0:
            rows.append((sym, iso, sv, tv, round(sv / tv, 4)))
    return rows


def squeeze_screen(min_days=15, z_min=1.5, mention_days=21, min_mentions=3, min_vol=300000):
    """Squeeze candidates = tickers whose latest short-ratio is anomalously HIGH vs their OWN
    trailing baseline (z-score) AND that are being talked up in our subs. Uses the z-score, not
    the absolute level (which is market-maker noise -- see module docstring). Returns sorted list
    of dicts."""
    import statistics
    c = get_connection()
    max_date = c.execute("SELECT MAX(date) d FROM short_volume").fetchone()["d"]
    fresh_cut = (datetime.date.fromisoformat(max_date) - datetime.timedelta(days=5)).isoformat()
    ment_ts = time.time() - mention_days * 86400
    ment = {r["ticker"]: (r["m"], r["s"]) for r in c.execute(
        """SELECT ticker, COUNT(*) m, AVG(sentiment_score) s FROM ticker_mentions
           WHERE created_utc >= ? GROUP BY ticker HAVING m >= ?""", (ment_ts, min_mentions)).fetchall()}
    out = []
    for t in ment:
        rows = c.execute("SELECT date, short_ratio, total_volume FROM short_volume "
                         "WHERE ticker=? ORDER BY date", (t,)).fetchall()
        if len(rows) < min_days:
            continue
        sr = [r["short_ratio"] for r in rows]
        base = sr[:-1]
        mu = statistics.mean(base)
        sd = statistics.pstdev(base) or 1e-9
        last = sr[-1]
        z = (last - mu) / sd
        if z < z_min or rows[-1]["total_volume"] < min_vol or rows[-1]["date"] < fresh_cut:
            continue
        mentions, sent = ment[t]
        out.append({"ticker": t, "date": rows[-1]["date"], "short_ratio": round(last, 3),
                    "baseline": round(mu, 3), "z": round(z, 2), "mentions": mentions,
                    "sentiment": round(sent or 0, 2), "volume": int(rows[-1]["total_volume"])})
    c.close()
    out.sort(key=lambda r: r["z"], reverse=True)
    return out


def finra_backfill(days=180, tickers=None, pause=0.3):
    """Backfill daily short-volume for the last `days` (weekdays), restricted to `tickers`
    (default: all tickers we have mentions for). One request per day. Returns rows written."""
    _ensure()
    if tickers is None:
        c = get_connection()
        tickers = {r["ticker"] for r in c.execute("SELECT DISTINCT ticker FROM ticker_mentions").fetchall()}
        c.close()
    conn = get_connection()
    n = 0
    today = datetime.date.today()
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        if d.weekday() >= 5:        # skip weekends
            continue
        rows = fetch_day(d.strftime("%Y%m%d"), tickers)
        if rows:
            conn.executemany("""INSERT OR REPLACE INTO short_volume
                (ticker, date, short_volume, total_volume, short_ratio) VALUES(?,?,?,?,?)""", rows)
            conn.commit()
            n += len(rows)
        time.sleep(pause)
    conn.close()
    return n
