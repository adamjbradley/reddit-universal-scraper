"""Trend identification — a first-class regime signal AND a filter/gate.

Price-based per instrument: +1 up / -1 down / 0 neutral, via close vs 200-DMA confirmed by
the 50-DMA. Two uses validated here:
  (a) gate equity capitulation-longs to UP-trends -> fixes the 2022 falling-knife loss;
  (b) fire euphoria-shorts only in DOWN-trends -> isolates the regime where shorts work.
Run: python -m backtest.trend
"""
import bisect
import statistics

from export.database import get_connection
from backtest.engine import _load_prices, _fwd


def trend_state(px, sym, date, slow=200, fast=50):
    """+1 up / -1 down / 0 neutral at `date` (uses the last close on/before date)."""
    if sym not in px:
        return 0
    dates, closes = px[sym]
    i = bisect.bisect_right(dates, date[:10]) - 1
    if i < slow:
        return 0
    sslow = sum(closes[i - slow + 1:i + 1]) / slow
    sfast = sum(closes[i - fast + 1:i + 1]) / fast
    c = closes[i]
    if c > sslow and sfast >= sslow:
        return 1
    if c < sslow and sfast <= sslow:
        return -1
    return 0


def _sig_dates(kind):
    c = get_connection()
    op = "<=0.15" if kind == "cap" else ">=0.85"
    d = [r["date"] for r in c.execute(
        f"SELECT date FROM aggregate_daily WHERE rrai_pct {op} ORDER BY date").fetchall()]
    c.close()
    return d


def _byyear(px, dates, instr, side, gate=None, H=10):
    yr = {}
    for d in dates:
        if gate is not None and trend_state(px, instr, d) != gate:
            continue
        r = _fwd(px, instr, d, H)
        if r is not None:
            yr.setdefault(d[:4], []).append(r * side)
    return yr


def _fmt(yr):
    return "  ".join(f"{y}:{statistics.mean(v)*100:+.1f}%(n{len(v)})" for y, v in sorted(yr.items()))


def run(H=10):
    px = _load_prices()
    caps, euph = _sig_dates("cap"), _sig_dates("euph")
    print(f"=== Trend-gated strategies, {H}d return by year ===\n")
    print("CAPITULATION-LONG  SPY (the leg that failed in the 2022 bear):")
    print("  ungated   :", _fmt(_byyear(px, caps, "SPY", +1)))
    print("  trend-up  :", _fmt(_byyear(px, caps, "SPY", +1, gate=1)))
    print("\nCAPITULATION-LONG  AUDJPY (does the filter hurt the already-robust leg?):")
    print("  ungated   :", _fmt(_byyear(px, caps, "AUDJPY=X", +1)))
    print("  trend-up  :", _fmt(_byyear(px, caps, "AUDJPY=X", +1, gate=1)))
    print("\nEUPHORIA-SHORT  SPY (only fire in downtrends?):")
    print("  ungated   :", _fmt(_byyear(px, euph, "SPY", -1)))
    print("  trend-down:", _fmt(_byyear(px, euph, "SPY", -1, gate=-1)))


if __name__ == "__main__":
    run()
