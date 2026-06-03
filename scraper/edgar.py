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
