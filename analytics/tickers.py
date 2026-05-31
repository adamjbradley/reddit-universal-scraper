"""Ticker extraction from post/comment text.

extract_tickers(text) -> set[str] of canonical (uppercase) US tickers.

High confidence: $CASHTAGS. Lower confidence: bare ALL-CAPS tokens, accepted only
if they're in the known-symbol universe AND not in the stoplist of common all-caps
words that double as tickers (DD, ALL, A, ...).
"""
import re
from pathlib import Path

import config

_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
# Bare tokens require >=2 chars - single uppercase letters ("U", "S") are almost
# always casual text, not tickers. Single-letter tickers are caught via $cashtag only.
_BARE = re.compile(r"\b([A-Z]{2,5})\b")

_SYMBOLS_FILE = config.DATA_DIR / "reference" / "symbols.txt"
_NASDAQ_URLS = [
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
]

# Common all-caps words / acronyms that are also valid tickers but rarely meant as such.
STOPLIST = {
    "A", "I", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS", "IT",
    "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE", "AM", "PM",
    "DD", "FD", "FDS", "TA", "PT", "TP", "SL", "IV", "PE", "EPS", "ATH", "ATL",
    "CEO", "CFO", "COO", "CTO", "IPO", "ETF", "ER", "AH", "EOD", "EOW", "YTD",
    "USA", "UK", "EU", "GDP", "FED", "SEC", "IRS", "FDA", "FBI", "CIA", "AI",
    "EV", "PC", "TV", "OK", "LOL", "WTF", "WSB", "YOLO", "FOMO", "FUD", "HODL",
    "IMO", "IMHO", "TLDR", "TLDR", "EDIT", "NSFW", "OP", "PSA", "FAQ", "AMA",
    "OTM", "ITM", "ATM", "DTE", "OI", "VWAP", "RSI", "MACD", "SMA", "EMA", "MA",
    "ROI", "PNL", "PL", "QE", "QT", "CPI", "PPI", "FOMC", "RH", "TD", "API",
    "URL", "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "ID", "OG", "GG", "EZ",
    "ALL", "ANY", "ARE", "BIG", "BUY", "CAN", "FOR", "GET", "GOT", "HAS", "HE",
    "HER", "HIS", "HOW", "ITS", "LET", "MAN", "NEW", "NOT", "NOW", "OUT", "OWN",
    "PUT", "SEE", "SHE", "THE", "TOO", "TRY", "WAS", "WAY", "WHO", "WHY", "YES",
    "RED", "WIN", "LOW", "TOP", "BAD", "FUN", "HOT", "JOB", "KEY", "RUN", "WAR",
    "EOY", "MOM", "DAD", "BRO", "GUY", "LMAO", "RIP", "DCA", "SP", "SPX", "DJI",
}

_symbols = None


def _load_symbols():
    global _symbols
    if _symbols is not None:
        return _symbols

    syms = set()
    # 1) cached file
    if _SYMBOLS_FILE.exists():
        try:
            syms = {l.strip().upper() for l in _SYMBOLS_FILE.read_text().splitlines() if l.strip()}
        except Exception:
            syms = set()

    # 2) download + cache (best effort)
    if not syms:
        try:
            import requests
            for url in _NASDAQ_URLS:
                r = requests.get(url, timeout=20)
                for line in r.text.splitlines()[1:]:
                    sym = line.split("|", 1)[0].strip().upper()
                    if sym and sym.isalpha() and 1 <= len(sym) <= 5:
                        syms.add(sym)
            if syms:
                _SYMBOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
                _SYMBOLS_FILE.write_text("\n".join(sorted(syms)))
        except Exception:
            syms = set()

    # 3) bundled fallback (major names + meme/ETF universe)
    if not syms:
        syms = set(_FALLBACK_SYMBOLS)

    _symbols = syms
    return _symbols


# Minimal fallback so extraction works offline even before the symbol list loads.
_FALLBACK_SYMBOLS = {
    "AAPL","MSFT","NVDA","AMZN","GOOG","GOOGL","META","TSLA","AMD","NFLX","INTC",
    "SPY","QQQ","IWM","DIA","VTI","VOO","ARKK","SOXL","TQQQ","SQQQ","UVXY","VXX",
    "GME","AMC","BB","NOK","PLTR","SOFI","NIO","LCID","RIVN","F","BAC","JPM","WFC",
    "BABA","COIN","HOOD","MARA","RIOT","MSTR","SMCI","AVGO","MU","TSM","ASML","ARM",
    "DIS","BA","XOM","CVX","PFE","MRNA","T","VZ","KO","PEP","WMT","COST","HD","NKE",
    "SHOP","SNAP","UBER","LYFT","PYPL","SQ","ABNB","CRM","ADBE","ORCL","CSCO","IBM",
    "DKNG","CHWY","CVNA","UPST","AFRM","DELL","ON","SE","RDDT","DJT","SPCE","MU",
}


def extract_tickers(text):
    """Return a set of canonical tickers found in text."""
    if not text:
        return set()
    found = set()
    # High-confidence cashtags
    for m in _CASHTAG.findall(text):
        sym = m.upper()
        if sym not in STOPLIST:
            found.add(sym)
    # Bare uppercase tokens validated against the symbol universe
    symbols = _load_symbols()
    for m in _BARE.findall(text):
        if m in STOPLIST:
            continue
        if m in symbols:
            found.add(m)
    return found
