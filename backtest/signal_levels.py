"""Signal-level sensitivity sweep for distribution_short — explore the gate thresholds with
regime-stability and the capacity<->conviction tradeoff.

The edge is parametrically ROBUST: vary any gate over a sensible range and it stays positive AND
regime-stable (a broad plateau, not a fragile peak). The sweep quantifies the capacity<->conviction
dial (looser gates = more signals, thinner per-trade edge) and flags the few settings that BREAK
regime-stability (per_author>=2.5, sentiment>=0.6, rollover window k=3). Re-run as data grows.

Three sweeps:
  sweep()           — the 4 GATE thresholds (pa/mentions/sentiment/rollover) + deployable tiers.
  feature_sweep()   — every SECONDARY feature terciled on the base signal -> short-return gradient.
  regime_validate() — the discipline that matters: does a feature's good half beat BASE in EVERY
                      regime? Pooled gradients lie — a "+16%, win 88%" feature (breadth, sent_delta,
                      rollover-depth) is worthless if it just up-weights 2025 (the regime that already
                      works) and goes negative in 2021. ONLY new-author recruitment (new_frac_trail /
                      its sibling new_z) survives: positive in every regime incl. 2021, where the base
                      signal is otherwise dead (+0.1%). It's thesis-perfect — a pump still pulling in
                      fresh retail has more crowd to distribute into. n is tiny (base ~78); a refinement
                      candidate to confirm as data grows, not yet a frozen gate.

  docker compose exec mcp python -m backtest.signal_levels [gates|features|regime|all]
"""
import sys
import math
import statistics
from collections import defaultdict

from backtest.microstructure import _rows, _trailing_ret
from backtest.engine import _load_prices, _net
from backtest.dist_short_oos import ETFS as BLOCK

H, SPREAD, BORROW = 10, 75, 8.0
BASE = dict(pa=2.0, mn=10, sent=0.4, k=5, thr=0.0)   # the validated default gate set
SECONDARY = ["mentions_z", "accel", "vel", "breadth", "concentration",
             "sent_delta", "new_frac_trail", "new_z", "new_auth_trail"]  # tested as extra filters

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


def _net_of(d):
    px, _ = _data()
    return _net(px, d["ticker"], d["date"], -1, H, SPREAD, BORROW, True)


def _feat(d, f):
    """Feature value; 'rollover_depth' is the trailing-5d return (more negative = deeper crack)."""
    if f == "rollover_depth":
        px, _ = _data()
        return _trailing_ret(px, d["ticker"], d["date"], 5)
    return d.get(f)


def _g(rs):
    if len(rs) < 4:
        return f"n{len(rs)} --"
    m = statistics.mean(rs)
    return f"{m * 100:+5.1f}% (n{len(rs)},w{100 * sum(1 for x in rs if x > 0) // len(rs)})"


def _base_nets(**gate):
    p = dict(BASE); p.update(gate)
    out = [(d, _net_of(d)) for d in signals(**p)]
    return [(d, x) for d, x in out if x is not None]


def feature_sweep(**gate):
    """On the base signal, tercile each secondary feature by value -> short-return gradient.
    A monotonic UP/DOWN gradient is a *candidate* refinement; confirm it with regime_validate()
    before believing it (pooled gradients are dominated by the 2025 regime). Small n -> noisy."""
    base = _base_nets(**gate)
    print(f"=== FEATURE-VALUE SWEEP on base signal (n={len(base)}) ===")
    print("    each feature terciled LOW/MID/HIGH -> short return; grad UP/DOWN = monotonic\n")
    print(f"  {'feature':16} {'LOW':>20} {'MID':>20} {'HIGH':>20}  grad")
    for f in SECONDARY + ["rollover_depth"]:
        vals = [(_feat(d, f), x) for d, x in base if _feat(d, f) is not None]
        if len(vals) < 15:
            continue
        vals.sort(key=lambda z: z[0]); k = len(vals) // 3
        lo = [x for _, x in vals[:k]]; mid = [x for _, x in vals[k:2 * k]]; hi = [x for _, x in vals[2 * k:]]
        g = ("UP" if statistics.mean(hi) > statistics.mean(lo) + 0.05
             else "DOWN" if statistics.mean(hi) < statistics.mean(lo) - 0.05 else "flat")
        print(f"  {f:16} {_g(lo):>20} {_g(mid):>20} {_g(hi):>20}  {g}")


def regime_validate(feats=("new_frac_trail", "new_z", "breadth", "sent_delta", "mentions", "rollover_depth"), **gate):
    """The test that separates a real refinement from a 2025-selection artifact: does the feature's
    above-median half beat BASE in EVERY regime (esp. 2021)? FRAGILE = fails 2021; that feature is a
    regime proxy, not an edge. Only new_frac_trail/new_z come back ROBUST/pos-all."""
    base = _base_nets(**gate)
    REGS = ("21", "22-24", "now")
    braw = defaultdict(list)
    for d, x in base:
        braw[_reg(d)].append(x)

    def stat(rs):
        return f"{statistics.mean(rs) * 100:+5.1f}%/n{len(rs):<2}" if rs else "   --   "
    print(f"=== REGIME-VALIDATION on base signal (n={len(base)}) ===")
    print("    a feature is REAL only if its good half stays positive in every regime, incl. 2021\n")
    print(f"  {'feature':18} | " + " | ".join(f"{r:>12}" for r in REGS) + " |  verdict")
    print(f"  {'(base)':18} | " + " | ".join(f"{stat(braw[r]):>12}" for r in REGS) + " |")
    for f in feats:
        deep = f == "rollover_depth"
        vals = [(_feat(d, f), d, x) for d, x in base if _feat(d, f) is not None]
        if len(vals) < 10:
            continue
        med = statistics.median([v for v, _, _ in vals])
        good = [(d, x) for v, d, x in vals if (v < med if deep else v >= med)]   # deep rollover = below median
        per = defaultdict(list)
        for d, x in good:
            per[_reg(d)].append(x)
        allpos = all(per[r] and statistics.mean(per[r]) > 0 for r in REGS)
        beats = all(per[r] and braw[r] and statistics.mean(per[r]) >= statistics.mean(braw[r]) - 0.01 for r in REGS)
        verdict = "ROBUST" if allpos and beats else ("pos-all" if allpos else "FRAGILE")
        lbl = f + ("(deep)" if deep else "(>med)")
        print(f"  {lbl:18} | " + " | ".join(f"{stat(per[r]):>12}" for r in REGS) + f" |  {verdict}")


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
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("gates", "all"):
        sweep()
    if which in ("features", "all"):
        print()
        feature_sweep()
    if which in ("regime", "all"):
        print()
        regime_validate()
