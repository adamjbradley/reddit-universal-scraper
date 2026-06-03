"""MT5 'optimizer' for distribution_short execution params, on real broker OHLC.
Grid over (hold, stop-loss, entry-offset); judged by IN-SAMPLE (2021-24) -> OUT-OF-SAMPLE (2025-26)
so we pick a ROBUST PLATEAU, not the overfit in-sample peak (the macro reckoning lesson)."""
import csv
import bisect
import datetime
import statistics
from collections import defaultdict

import MetaTrader5 as mt5

if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe"):
    print("init failed:", mt5.last_error()); raise SystemExit

BENCH = next((c for c in ("VOO", "US500", "IWM") if mt5.symbol_info(c)), None)
COST = 0.023
FROM, TO = datetime.datetime(2020, 1, 1), datetime.datetime(2026, 6, 4)


def ohlc(sym):
    mt5.symbol_select(sym, True)
    rr = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
    if rr is None or len(rr) == 0:
        mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 3000)
        rr = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
    if rr is None or len(rr) == 0:
        return None
    return ([datetime.datetime.utcfromtimestamp(x["time"]).strftime("%Y-%m-%d") for x in rr],
            [float(x["high"]) for x in rr], [float(x["close"]) for x in rr])


sig = defaultdict(list)
with open(r"C:\Users\abradley\AppData\Local\Temp\dist_short_signals.csv") as f:
    for row in csv.DictReader(f):
        sig[row["ticker"]].append((row["signal_date"], row["year"]))

bench = ohlc(BENCH)
bdate = {d: c for d, c in zip(bench[0], bench[2])}
data = {t: ohlc(t) for t in sig if mt5.symbol_info(t)}
data = {t: v for t, v in data.items() if v}
print(f"benchmark={BENCH}; {len(data)}/{len(sig)} symbols loaded\n")


def trade(dc, sd, hold, stop, eoff):
    dates, highs, closes = dc
    i = bisect.bisect_right(dates, sd) + eoff
    if i >= len(closes) or i not in range(len(closes)):
        return None
    entry = closes[i]
    if entry <= 0:
        return None
    e = min(i + hold, len(closes) - 1)
    if stop is not None:                      # short stop: exit if stock rises `stop` intraday
        for j in range(i + 1, e + 1):
            if highs[j] >= entry * (1 + stop):
                e = j
                exit_px = entry * (1 + stop)
                break
        else:
            exit_px = closes[e]
    else:
        exit_px = closes[e]
    r_stock = exit_px / entry - 1
    b0, b1 = bdate.get(dates[i]), bdate.get(dates[e])
    r_bench = (b1 / b0 - 1) if (b0 and b1) else 0.0
    return -(r_stock - r_bench) - COST        # short, market-neutral, after cost


def evaluate(hold, stop, eoff):
    isn, oosn = [], []
    for t, dc in data.items():
        for sd, yr in sig[t]:
            r = trade(dc, sd, hold, stop, eoff)
            if r is None:
                continue
            (oosn if yr in ("2025", "2026") else isn).append(r)
    return isn, oosn


def m(rs):
    return statistics.mean(rs) * 100 if len(rs) >= 2 else None


print("=== grid: (hold, stop, entry-offset) -> IS 2021-24 / OOS 2025-26 net/trade ===")
rows = []
for hold in (5, 10, 15, 20):
    for stop in (None, 0.50, 0.30, 0.20):    # positive = stock-rise that stops the short (caps loss)
        for eoff in (0, 1):
            isn, oosn = evaluate(hold, stop, eoff)
            mi, mo = m(isn), m(oosn)
            if mi is None or mo is None:
                continue
            rows.append((hold, stop, eoff, mi, mo, len(isn) + len(oosn)))

rows.sort(key=lambda r: -r[3])                # rank by IN-SAMPLE (what the naive optimizer maximizes)
print(f"  {'hold':>4} {'stop':>6} {'eoff':>4} {'IS%':>7} {'OOS%':>7}  n")
print("  -- top 6 by IN-SAMPLE (the overfit ranking) --")
for h, s, e, mi, mo, n in rows[:6]:
    print(f"  {h:>4} {str(s):>6} {e:>4} {mi:>+7.2f} {mo:>+7.2f}  {n}")
print("  -- ranked by min(IS,OOS) = robust across both (the deployable choice) --")
for h, s, e, mi, mo, n in sorted(rows, key=lambda r: -min(r[3], r[4]))[:6]:
    print(f"  {h:>4} {str(s):>6} {e:>4} {mi:>+7.2f} {mo:>+7.2f}  {n}")
# the current/default config for reference
cur = [r for r in rows if r[0] == 10 and r[1] is None and r[2] == 0]
if cur:
    h, s, e, mi, mo, n = cur[0]
    print(f"\n  current default (hold10, no-stop, eoff0): IS {mi:+.2f}% / OOS {mo:+.2f}%")
mt5.shutdown()
