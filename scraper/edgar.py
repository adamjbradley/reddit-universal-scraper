"""SEC EDGAR filing fetcher — dilution/offering signals for micro-cap pumps.

The mechanism behind manufactured micro-cap pumps: the company (or insiders) sell shares
INTO the retail volume the pump creates. The tradeable, point-in-time event is the OFFERING
FILING itself (424B5 prospectus supplement, S-1/S-3 registration, ATM program) — known the day
it's filed. This module maps ticker->CIK and returns offering filing dates per ticker.

Free, no API key; SEC requires a declared User-Agent and ~10 req/s max.
"""
import requests

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
    global _CIK
    if _CIK is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30)
        _CIK = {d["ticker"].upper(): str(d["cik_str"]).zfill(10) for d in r.json().values()}
    return _CIK


def filings(ticker, forms=None):
    """[(form, filingDate)] from EDGAR's recent-submissions feed (last ~1000 filings)."""
    cik = _cikmap().get(ticker.upper())
    if not cik:
        return []
    try:
        s = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=UA, timeout=30).json()
    except Exception:
        return []
    rec = s.get("filings", {}).get("recent", {})
    out = list(zip(rec.get("form", []), rec.get("filingDate", [])))
    return [(f, d) for f, d in out if forms is None or f in forms]


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
    try:
        s = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=UA, timeout=30).json()
    except Exception:
        return []
    rec = s.get("filings", {}).get("recent", {})
    forms = rec.get("form", []); accs = rec.get("accessionNumber", [])
    docs = rec.get("primaryDocument", []); dates = rec.get("filingDate", [])
    buys = set()
    seen = 0
    for form, acc, doc, fdate in zip(forms, accs, docs, dates):
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
