"""MT5 backtest of distribution_short on broker OHLC — overall, by regime, and by deployable
liquidity tier ($5-50 = what we'd actually trade). Market-neutral vs VOO, 10d, costs."""
import csv
import bisect
import datetime
import statistics
from collections import defaultdict

import MetaTrader5 as mt5

if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe"):
    print("init failed:", mt5.last_error()); raise SystemExit

idx = None
for cand in ("VOO", "US500", "IWM"):
    if mt5.symbol_info(cand) is not None:
        idx = cand
        break
print("market-neutral benchmark:", idx)

sig = defaultdict(list)
with open(r"C:\Users\abradley\AppData\Local\Temp\dist_short_signals.csv") as f:
    for row in csv.DictReader(f):
        try:
            spx = float(row["signal_px"]) if row["signal_px"] else None
        except ValueError:
            spx = None
        sig[row["ticker"]].append((row["signal_date"], row["year"], spx))

avail = [(t, len(sig[t])) for t in sig if mt5.symbol_info(t) is not None]
print(f"signal tickers available in MT5: {len(avail)} / {len(sig)}")

FROM, TO = datetime.datetime(2020, 1, 1), datetime.datetime(2026, 6, 4)


def rates(sym):
    mt5.symbol_select(sym, True)
    rr = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
    if rr is None or len(rr) == 0:
        mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 3000)
        rr = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, FROM, TO)
    if rr is None or len(rr) == 0:
        return None
    return ([datetime.datetime.utcfromtimestamp(x["time"]).strftime("%Y-%m-%d") for x in rr],
            [float(x["close"]) for x in rr])


def fwd(dc, sigdate, h=10):
    dates, closes = dc
    i = bisect.bisect_right(dates, sigdate)
    if i >= len(closes) or i + h >= len(closes):
        return None
    return (closes[i + h] / closes[i] - 1) if closes[i] > 0 else None


idxr = rates(idx)
COST = 0.015 + 0.008


def _tier(p):
    if p is None:
        return "?"
    if p < 5:
        return "<$5 thin"
    if p <= 50:
        return "$5-50 TRADEABLE"
    return ">$50 large"


print("\n=== MT5 distribution_short backtest (short, 10d, mkt-neutral vs VOO, broker OHLC) ===")
allnets, byyear, bytier = [], defaultdict(list), defaultdict(list)
for t, n in avail:
    dc = rates(t)
    if not dc:
        continue
    for sd, yr, spx in sig[t]:
        g, m = fwd(dc, sd), fwd(idxr, sd)
        if g is None or m is None:
            continue
        net = -(g - m) - COST
        allnets.append(net); byyear[yr].append(net); bytier[_tier(spx)].append(net)


def _line(lbl, rs):
    if len(rs) < 2:
        return f"  {lbl:18} n={len(rs)} (too few)"
    w = 100 * sum(1 for x in rs if x > 0) / len(rs)
    return f"  {lbl:18} n={len(rs):>3} net={statistics.mean(rs)*100:+6.2f}% win={w:>3.0f}%"


print(_line("OVERALL", allnets))
print("  by regime:")
for yr in sorted(byyear):
    print("  " + _line(yr, byyear[yr]))
print("  by LIQUIDITY tier (deployable cut):")
for tier in ("<$5 thin", "$5-50 TRADEABLE", ">$50 large"):
    if bytier.get(tier):
        print("  " + _line(tier, bytier[tier]))
mt5.shutdown()
