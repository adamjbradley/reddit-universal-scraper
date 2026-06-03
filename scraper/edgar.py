"""SEC EDGAR filing fetcher — dilution/offering signals for micro-cap pumps.

The mechanism behind manufactured micro-cap pumps: the company (or insiders) sell shares
INTO the retail volume the pump creates. The tradeable, point-in-time event is the OFFERING
FILING itself (424B5 prospectus supplement, S-1/S-3 registration, ATM program) — known the day
it's filed. This module maps ticker->CIK and returns offering filing dates per ticker.

Free, no API key; SEC requires a declared User-Agent and ~10 req/s max.
"""
import time
import requests

from export.database import get_connection

UA = {"User-Agent": "reddit-signals-research adam_j_bradley@yahoo.com"}

# Forms that register or effect a securities sale = dilution / shelf takedown.
OFFERING = {"424B5", "424B3", "424B4", "424B2", "424B1", "424B7",
            "S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "F-1", "F-1/A", "F-3",
            "FWP", "EFFECT"}
# Insider transactions (Form 4 = changes in beneficial ownership; sells are bearish).
INSIDER = {"4", "4/A"}
# Notable-holder / "smart money" stake disclosures.
ACTIVIST = {"SC 13D", "SC 13D/A"}        # >5% stake WITH intent to influence -> often moves price
PASSIVE = {"SC 13G", "SC 13G/A"}         # >5% passive stake
INSTITUTIONAL = {"13F-HR", "13F-HR/A"}   # quarterly fund holdings (45-day lag)

_CIK = None


def _cikmap():
    """ticker->CIK map, cached in the DB (edgar_cik) so a fresh process / a 429 on the live
    file doesn't re-fetch or break. Falls back to live fetch only when the cache is empty."""
    global _CIK
    if _CIK is not None:
        return _CIK
    c = get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS edgar_cik (ticker TEXT PRIMARY KEY, cik TEXT)")
    rows = c.execute("SELECT ticker, cik FROM edgar_cik").fetchall()
    if rows:
        _CIK = {r["ticker"]: r["cik"] for r in rows}
        c.close()
        return _CIK
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30)
    _CIK = {d["ticker"].upper(): str(d["cik_str"]).zfill(10) for d in r.json().values()}
    c.executemany("INSERT OR REPLACE INTO edgar_cik VALUES (?,?)", list(_CIK.items()))
    c.commit()
    c.close()
    return _CIK


def _sub_cache_get(ticker):
    c = get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS edgar_submissions "
              "(ticker TEXT, form TEXT, filing_date TEXT, accession TEXT, primary_doc TEXT)")
    rows = c.execute("SELECT form, filing_date, accession, primary_doc FROM edgar_submissions "
                     "WHERE ticker=?", (ticker.upper(),)).fetchall()
    c.close()
    return [(r["form"], r["filing_date"], r["accession"], r["primary_doc"]) for r in rows] or None


def _sub_cache_put(ticker, rows):
    c = get_connection()
    c.execute("CREATE TABLE IF NOT EXISTS edgar_submissions "
              "(ticker TEXT, form TEXT, filing_date TEXT, accession TEXT, primary_doc TEXT)")
    c.execute("DELETE FROM edgar_submissions WHERE ticker=?", (ticker.upper(),))
    c.executemany("INSERT INTO edgar_submissions VALUES (?,?,?,?,?)",
                  [(ticker.upper(), f, d, a, p) for f, d, a, p in rows])
    c.commit()
    c.close()


def _submissions(ticker):
    """All (form, filingDate, accession, primaryDoc) for a ticker — DB-cached read-through.
    First call per ticker hits EDGAR and stores; later calls (even new processes) read the DB,
    so a 150-ticker drill fetches each name once, ever, instead of re-hammering SEC -> no 429."""
    cached = _sub_cache_get(ticker)
    if cached is not None:
        return cached
    cik = _cikmap().get(ticker.upper())
    if not cik:
        return []
    try:
        s = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=UA, timeout=30)
        if s.status_code == 429:
            time.sleep(2)
            return []                          # don't poison the cache on a throttle
        rec = s.json().get("filings", {}).get("recent", {})
    except Exception:
        return []
    rows = list(zip(rec.get("form", []), rec.get("filingDate", []),
                    rec.get("accessionNumber", []), rec.get("primaryDocument", [])))
    if rows:
        _sub_cache_put(ticker, rows)
    return rows


def filings(ticker, forms=None):
    """[(form, filingDate)] from the cached submissions feed."""
    return [(f, d) for f, d, _, _ in _submissions(ticker) if forms is None or f in forms]


def offering_dates(ticker):
    """Sorted unique dates on which `ticker` filed an offering/dilution form."""
    return sorted({d for _, d in filings(ticker, OFFERING)})


def insider_dates(ticker):
    """Sorted dates of Form-4 insider-transaction filings (sign not parsed here)."""
    return sorted({d for _, d in filings(ticker, INSIDER)})


def stake_dates(ticker, forms):
    """Sorted unique filing dates for the given form set (e.g. ACTIVIST 13D)."""
    return sorted({d for _, d in filings(ticker, forms)})


def form4_buys(ticker, limit=60):
    """Parse recent Form-4 docs into open-market BUY days. Returns sorted dates on which an
    insider had a non-trivial open-market PURCHASE (transactionCode 'P'). Best-effort XML parse;
    skips anything it can't read. (Open-market buys are the documented bullish insider signal.)"""
    import re
    cik = _cikmap().get(ticker.upper())
    if not cik:
        return []
    buys = set()
    seen = 0
    for form, fdate, acc, doc in _submissions(ticker):
        if form not in INSIDER or not doc.endswith(".xml"):
            continue
        seen += 1
        if seen > limit:
            break
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"
        try:
            xml = requests.get(url, headers=UA, timeout=30).text
        except Exception:
            continue
        # any non-derivative open-market purchase (code P) with shares > 0
        for blk in re.findall(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml, re.S):
            code = re.search(r"<transactionCode>\s*([A-Z])\s*</transactionCode>", blk)
            shares = re.search(r"<transactionShares>.*?<value>\s*([\d.]+)", blk, re.S)
            if code and code.group(1) == "P" and shares and float(shares.group(1)) > 0:
                buys.add(fdate)
                break
    return sorted(buys)
