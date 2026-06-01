"""Cyclical / calendar effects on SPY (10y price history -> well-powered, unlike our ~1yr
Reddit signal). Pure price seasonality here; the plan is to LAYER the Reddit signals on top
(e.g. only take capitulation-longs in a favourable calendar window). Run:
  python -m backtest.calendar_events
"""
import statistics
from datetime import date
from backtest.engine import _load_prices


def _spy_series():
    px = _load_prices()
    dates, closes = px.get("SPY", ([], []))
    return dates, closes


def _is_opex(d):           # monthly options expiry = 3rd Friday
    dd = date.fromisoformat(d[:10])
    return dd.weekday() == 4 and 15 <= dd.day <= 21


def _is_opex_week(d):
    dd = date.fromisoformat(d[:10])
    # the week (Mon-Fri) containing the 3rd Friday
    third_fri = [day for day in range(15, 22) if date(dd.year, dd.month, day).weekday() == 4][0]
    return abs(dd.day - third_fri) <= 4 and dd.day <= third_fri + 0


def run(horizon=5):
    dates, closes = _spy_series()
    n = len(dates)
    if n < 300:
        print("not enough SPY history"); return
    print(f"=== SPY cyclical effects ({len(dates)} days, fwd {horizon}d) ===\n")

    def fwd(i):
        if i + horizon >= n or closes[i] <= 0:
            return None
        return closes[i + horizon] / closes[i] - 1

    def report(label, buckets):
        for name, idxs in buckets:
            rs = [fwd(i) for i in idxs]; rs = [x for x in rs if x is not None]
            if len(rs) >= 10:
                m = statistics.mean(rs); win = sum(1 for x in rs if x > 0) / len(rs) * 100
                sd = statistics.pstdev(rs) or 1e-9
                t = m / (sd / len(rs) ** 0.5)
                print(f"  {label:18s} {name:14s} n={len(rs):<4} avg={m*100:+.2f}%  win={win:.0f}%  t={t:+.2f}")
        print()

    # turn-of-month: last 3 + first 3 trading days of a calendar month
    tom, other = [], []
    months = {}
    for i, d in enumerate(dates):
        months.setdefault(d[:7], []).append(i)
    tom_set = set()
    for m, idxs in months.items():
        for k in idxs[:3] + idxs[-3:]:
            tom_set.add(k)
    report("turn-of-month", [("TOM (±3 days)", [i for i in range(n) if i in tom_set]),
                             ("rest of month", [i for i in range(n) if i not in tom_set])])

    # OpEx week vs rest
    report("opex", [("OpEx week", [i for i, d in enumerate(dates) if _is_opex_week(d)]),
                    ("non-OpEx", [i for i, d in enumerate(dates) if not _is_opex_week(d)])])

    # day of week
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    report("day-of-week", [(dow_names[w], [i for i, d in enumerate(dates)
                                            if date.fromisoformat(d[:10]).weekday() == w]) for w in range(5)])

    # month-of-year seasonality (sample counts shown; ~10y so ~10/month)
    mo = [("%02d" % m, [i for i, d in enumerate(dates) if int(d[5:7]) == m]) for m in range(1, 13)]
    report("month-of-year", mo)


if __name__ == "__main__":
    run()
