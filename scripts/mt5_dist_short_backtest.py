"""MT5 backtest of distribution_short on small/mid-cap signal names, using ACTUAL broker OHLC.
Matches the Python methodology: enter at first close strictly after the signal date, hold 10
sessions, market-neutral vs the S&P index, 75bps spread + 8bps/day borrow."""
import csv
import bisect
import datetime
import statistics
from collections import defaultdict

import MetaTrader5 as mt5

if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe"):
    print("init failed:", mt5.last_error()); raise SystemExit

# --- market-neutral benchmark: VOO (S&P 500 ETF, full history, matches the Python SPY leg) ---
idx = None
for cand in ("VOO", "US500", "IWM"):
    if mt5.symbol_info(cand) is not None:
        idx = cand
        break
print("market-neutral benchmark:", idx)

# --- load signals ---
sig = defaultdict(list)
with open(r"C:\Users\abradley\AppData\Local\Temp\dist_short_signals.csv") as f:
    for row in csv.DictReader(f):
        sig[row["ticker"]].append((row["signal_date"], row["year"]))

# --- which signal tickers are tradeable in MT5? ---
avail = []
for t in sig:
    info = mt5.symbol_info(t)
    if info is not None:
        avail.append((t, len(sig[t]), info.spread, info.trade_mode))
avail.sort(key=lambda x: -x[1])
print(f"\nsignal tickers available in MT5: {len(avail)} / {len(sig)}")
print("  (ticker, #signals, spread_pts, trade_mode)")
for a in avail[:15]:
    print("   ", a)

FROM, TO = datetime.datetime(2020, 1, 1), datetime.datetime(2026, 6, 4)


def rates(sym):
    mt5.symbol_select(sym, True)
    rr = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
    if rr is None or len(rr) == 0:
        mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 3000)   # force lazy history download
        rr = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
    if rr is None or len(rr) == 0:
        return None
    dates = [datetime.datetime.utcfromtimestamp(x["time"]).strftime("%Y-%m-%d") for x in rr]
    return dates, [float(x["close"]) for x in rr]


def fwd(dc, sigdate, h=10):
    dates, closes = dc
    i = bisect.bisect_right(dates, sigdate)
    if i >= len(closes) or i + h >= len(closes):
        return None
    p0 = closes[i]
    return (closes[i + h] / p0 - 1) if p0 > 0 else None


idxr = rates(idx) if idx else None
COST = 0.015 + 0.008                      # 75bps round-trip spread + 8bps/day*10 borrow

print("\n=== MT5 distribution_short backtest (short, 10d, market-neutral, on broker OHLC) ===")
allnets, allyr = [], defaultdict(list)
per = []
for t, nsig, spread, tm in avail:
    dc = rates(t)
    if not dc:
        continue
    nets = []
    for sd, yr in sig[t]:
        g = fwd(dc, sd)
        m = fwd(idxr, sd) if idxr else 0.0
        if g is None or m is None:
            continue
        net = -(g - m) - COST            # short, market-neutral, after costs
        nets.append(net); allnets.append(net); allyr[yr].append(net)
    if nets:
        per.append((t, len(nets), statistics.mean(nets) * 100, spread))
per.sort(key=lambda x: -x[1])
print("  per symbol:")
for t, n, net, spread in per:
    print(f"    {t:6} n={n:>2} net={net:+7.2f}%  (broker spread {spread} pts)")
if allnets:
    w = 100 * sum(1 for x in allnets if x > 0) / len(allnets)
    print(f"\n  OVERALL: n={len(allnets)} net={statistics.mean(allnets)*100:+.2f}% win={w:.0f}%")
    print("  by year:")
    for yr in sorted(allyr):
        v = allyr[yr]
        print(f"    {yr}: n={len(v):>2} net={statistics.mean(v)*100:+7.2f}%")
mt5.shutdown()
