"""Python replica of the MT5 RedditMacro_EA capitulation strategy — uses the EXACT broker OHLC
pulled from the terminal (mt5_ohlc table, via scripts/export_mt5_ohlc.py), so it reproduces the
MT5 tester including stops and drawdown, MT5-free and reproducible.

Replicates the EA's tester logic on the VIX-gated capitulation dates:
  - ARM on a capitulation signal, ENTER at the bar OPEN on the first up-bar (the "turn") within
    ArmWindow (or immediately if turn=False),
  - true-range ATR; ATR stop at StopATR x ATR checked INTRADAY on the bar low; time-exit after `hold` bars,
  - risk-based sizing: a stop-out loses `riskPct`% of equity (DD comparable across legs),
  - one position at a time (overlapping signals skipped while in a trade).
Run: python -m backtest.mt5_sim
"""
import statistics

from export.database import get_connection
from backtest.fear_gate import capitulation_sets


def _series(sym):
    c = get_connection()
    rows = c.execute("SELECT date, open, high, low, close FROM mt5_ohlc WHERE symbol=? ORDER BY date",
                     (sym,)).fetchall()
    c.close()
    return ([r["date"] for r in rows], [r["open"] for r in rows], [r["high"] for r in rows],
            [r["low"] for r in rows], [r["close"] for r in rows])


def _atr(highs, lows, closes, i, period):
    """True-range ATR over the `period` bars ending at i-1 (known at bar i)."""
    if i < period + 1:
        return 0.0
    trs = [max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1]))
           for k in range(i - period, i)]
    return statistics.mean(trs)


def simulate(sym, dateset, turn=True, arm=5, stopATR=3.0, hold=11, atrp=14, riskPct=1.0,
             start=None, end=None):
    """Run the EA mechanics over `dateset` (entry-trigger dates) on sym's broker OHLC."""
    dates, o, h, l, cl = _series(sym)
    if len(dates) < atrp + 5:
        return None
    trig = set(d for d in dateset if (start is None or d >= start) and (end is None or d < end))
    equity, peak, maxdd = 1.0, 1.0, 0.0
    pnls = []
    in_pos = armed = False
    arm_n = held = 0
    entry_px = stop_lvl = atr_e = 0.0
    for i in range(2, len(dates)):
        if start and dates[i] < start:
            continue
        if end and dates[i] >= end:
            break
        if in_pos:
            stopped = l[i] <= stop_lvl                       # intraday stop on the bar LOW
            held += 1
            if stopped or held >= hold:
                exit_px = stop_lvl if stopped else cl[i]
                r = exit_px / entry_px - 1.0
                stopfrac = (stopATR * atr_e) / entry_px if (stopATR > 0 and atr_e > 0) else 0.01
                pnl = (r / stopfrac) * (riskPct / 100.0) if stopfrac > 0 else 0.0
                equity *= (1.0 + pnl)
                peak = max(peak, equity)
                maxdd = max(maxdd, (peak - equity) / peak)
                pnls.append(pnl)
                in_pos = False
            continue
        if dates[i] in trig:
            armed, arm_n = True, 0
        if armed:
            arm_n += 1
            if (not turn) or (cl[i - 1] > cl[i - 2]):        # turn = prior bar closed up
                atr_e = _atr(h, l, cl, i, atrp)
                if atr_e > 0:
                    entry_px = o[i]                          # enter at the bar OPEN (market)
                    stop_lvl = entry_px - stopATR * atr_e
                    held, in_pos, armed = 0, True, False
            elif arm_n > arm:
                armed = False
    n = len(pnls)
    if n < 5:
        return {"n": n, "note": "too few"}
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf")
    return {"n": n, "win": 100.0 * len(wins) / n, "pf": pf,
            "net_pct": (equity - 1.0) * 100.0, "maxdd_pct": maxdd * 100.0}


def run():
    sets = capitulation_sets()
    vix = sets["vixgated"]
    # robust per-instrument configs (the deployable set); gold/AUDJPY are the live basket
    cfg = {
        "XAUUSD": dict(turn=True, arm=5, stopATR=3.0, hold=11),
        "AUDJPY": dict(turn=True, arm=5, stopATR=2.0, hold=24, riskPct=0.5),
        "AUDUSD": dict(turn=True, arm=5, stopATR=2.0, hold=10),
        "XAGUSD": dict(turn=True, arm=5, stopATR=3.0, hold=11),
        "US500":  dict(turn=True, arm=5, stopATR=3.0, hold=11),
        "USTEC":  dict(turn=True, arm=5, stopATR=3.0, hold=11),
    }
    print("=== Python replica of the MT5 EA on the EXACT broker OHLC (mt5_ohlc) ===")
    print("    true-range ATR + intraday stops -> should track the MT5 tester\n")
    print(f"  {'leg':8} {'window':12} {'n':>3} {'win%':>5} {'PF':>5} {'net%':>8} {'maxDD%':>7}")
    for sym, c in cfg.items():
        for lbl, s, e in (("full 2018-26", None, None),
                          ("IS 2018-23", "2018-01-01", "2023-01-01"),
                          ("OOS 2023-26", "2023-01-01", None)):
            r = simulate(sym, vix, start=s, end=e, **c)
            if not r or "note" in r:
                print(f"  {sym:8} {lbl:12} {'(too few)':>20}")
                continue
            print(f"  {sym:8} {lbl:12} {r['n']:>3} {r['win']:>5.0f} {r['pf']:>5.2f} "
                  f"{r['net_pct']:>+8.1f} {r['maxdd_pct']:>7.1f}")
        print()


if __name__ == "__main__":
    run()
