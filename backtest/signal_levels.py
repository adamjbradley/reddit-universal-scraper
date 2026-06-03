"""Signal-level sensitivity sweep for distribution_short — explore the gate thresholds with
regime-stability and the capacity<->conviction tradeoff.

The edge is parametrically ROBUST: vary any gate over a sensible range and it stays positive AND
regime-stable (a broad plateau, not a fragile peak). The sweep quantifies the capacity<->conviction
dial (looser gates = more signals, thinner per-trade edge) and flags the few settings that BREAK
regime-stability (per_author>=2.5, sentiment>=0.6, rollover window k=3). Re-run as data grows.

  docker compose exec mcp python -m backtest.signal_levels
"""
import math
import statistics
from collections import defaultdict

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import _load_prices, _net
from backtest.dist_short_oos import ETFS as BLOCK

H, SPREAD, BORROW = 10, 75, 8.0
BASE = dict(pa=2.0, mn=10, sent=0.4, k=5, thr=0.0)   # the validated default gate set

_PX = _ROWS = None


def _data():
    global _PX, _ROWS
    if _PX is None:
        _PX = _load_prices()
        _ROWS = [d for d in _rows(lookback_days=2200)
                 if d["new_frac_trail"] is not None and d["ticker"] not in BLOCK]
    return _PX, _ROWS


def signals(pa=2.0, mn=10, sent=0.4, k=5, thr=0.0):
    """Signals for a gate set: concentrated pump (pa/mn) + euphoria (sent) + rollover (trailing-k<thr)."""
    px, rows = _data()
    out = []
    for d in rows:
        if (d["per_author"] or 0) < pa or (d["mentions"] or 0) < mn or (d["sentiment"] or -9) < sent:
            continue
        tr = _trailing_ret(px, d["ticker"], d["date"], k)
        if tr is None or tr >= thr:
            continue
        out.append(d)
    return out


def _reg(d):
    y = d["date"][:4]
    return "21" if y == "2021" else ("22-24" if y in ("2022", "2023", "2024") else "now")


def evaluate(ev):
    """n / net / win / t / per-regime nets / robust flag for a signal set."""
    px, _ = _data()
    rows = [(_net(px, d["ticker"], d["date"], -1, H, SPREAD, BORROW, True), _reg(d)) for d in ev]
    rows = [(x, r) for x, r in rows if x is not None]
    if len(rows) < 3:
        return None
    allv = [x for x, _ in rows]
    m = statistics.mean(allv); sd = statistics.pstdev(allv) or 1e-9
    by = defaultdict(list)
    for x, r in rows:
        by[r].append(x)
    regs = {k: statistics.mean(by[k]) * 100 for k in ("21", "22-24", "now") if len(by.get(k, [])) >= 2}
    chk = {k: statistics.mean(v) for k, v in by.items() if len(v) >= 3}
    return dict(n=len(allv), net=m * 100, win=100 * sum(1 for x in allv if x > 0) / len(allv),
                t=m / (sd / math.sqrt(len(allv))), regs=regs,
                robust=bool(chk) and all(v > 0 for v in chk.values()))


def _row(label, **kw):
    p = dict(BASE); p.update(kw)
    r = evaluate(signals(**p))
    if r is None:
        print(f"  {label:18} (too few)")
        return
    rs = " ".join(f"{k}:{v:+.0f}" for k, v in r["regs"].items())
    print(f"  {label:18} n={r['n']:>3} net={r['net']:+6.2f}% win={r['win']:>3.0f}% "
          f"t={r['t']:+.2f}  [{rs}] {'ROBUST' if r['robust'] else ''}")


def sweep():
    print("=== distribution_short SIGNAL-LEVEL SWEEP (base = pa>=2, m>=10, sent>=0.4, trail-5d<0) ===")
    print("    regimes [2021 / 2022-24 / now]; ROBUST = positive every regime (n>=3)\n")
    _row("BASE")
    print("\n  per_author (concentration — strongest lever):")
    for pa in (1.5, 2.0, 2.5, 3.0, 4.0):
        _row(f"pa>={pa}", pa=pa)
    print("  mentions (volume — capacity knob):")
    for mn in (5, 8, 10, 15, 20):
        _row(f"m>={mn}", mn=mn)
    print("  sentiment (euphoria):")
    for s in (0.2, 0.3, 0.4, 0.5, 0.6):
        _row(f"sent>={s}", sent=s)
    print("  rollover depth (trailing-5d):")
    for thr in (0.05, 0.0, -0.05, -0.10):
        _row(f"trail<{thr:+.2f}", thr=thr)
    print("  rollover window (k days):")
    for k in (3, 5, 10):
        _row(f"k={k}", k=k)
    print("\n  === deployable tiers (capacity <-> conviction) ===")
    _row("CONVICTION pa>=3", pa=3.0)
    _row("BALANCED base")
    _row("CAPACITY m>=5", mn=5)


if __name__ == "__main__":
    sweep()
